"""Command-style execution scheduler (replaces the declarative ActLLMOngoingTasks).

``ExecutionScheduler`` is a sharpy ``ActBase`` driven every frame. It walks an
ordered list of :class:`PlannedAction` and enforces:

* **prerequisite / tech-chain checks** via ``data_tools.prereq_runtime`` (obs
  three-state aware): blocked actions WAIT for their prerequisites; missing
  prerequisites are not inserted automatically.
* **single-slot wait queue**: at most one action may be in ``WAITING`` at a time; it is
  abandoned after ``wait_abandon_sec`` if still blocked, otherwise issued as soon as
  resources / tech / supply allow.
* **resource reservation + overtake**: the waiting action reserves its mineral/gas/supply
  (actions that do not contend for the reserved funds).
* **SCV pre-move**: a build action waiting on resources sends a worker to the
  build site early.
* **execution split**: ``train/addon/morph`` pick an executor (rule candidates +
  executor LLM), ``build/research`` delegate to a lazily-created sharpy Act.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from sharpy.plans.acts import ActBase

from SC2_Agent.data_tools import (
    chain_in_progress,
    is_available_now,
)
from SC2_Agent.data_tools.supply_planner import current_free_supply
from SC2_Agent.execution import mapping
from SC2_Agent.execution.command import (
    ABANDONED,
    DONE,
    PENDING,
    RUNNING,
    WAITING,
    PlannedAction,
)
from SC2_Agent.execution import executor_select

logger = logging.getLogger("SC2_Agent.execution.scheduler")

#: 22.4 game frames per second on "Faster" speed.
FRAMES_PER_SECOND = 22.4
#: Executor-LLM result cache lifetime (seconds) to throttle execution-time calls.
EXECUTOR_CACHE_SEC = 3.0

TERRAN_PRODUCTION_FLYING_EQUIVALENTS = {
    UnitTypeId.BARRACKS: UnitTypeId.BARRACKSFLYING,
    UnitTypeId.FACTORY: UnitTypeId.FACTORYFLYING,
    UnitTypeId.STARPORT: UnitTypeId.STARPORTFLYING,
}


#: Type of the executor-LLM callback the bot injects.
#: (ability_name, candidate_text, cost_hint, pending_summary,
#:  waiting_summary, conflict_hints, legal_tags, tag_map) -> Optional[int]
ExecutorLLM = Callable[..., Optional[int]]


class ExecutionScheduler(ActBase):
    def __init__(self, wait_abandon_sec: float = 20.0, running_abandon_sec: float = 25.0):
        super().__init__()
        self.actions: List[PlannedAction] = []
        self.wait_abandon_sec = wait_abandon_sec
        #: build/research 动作在 RUNNING 状态停留超过该秒数仍未下单成功（例如
        #: 找不到落点导致 act 反复返回 False）即放弃，避免单个动作永久阻塞 macro。
        #: 正常 build 在 worker 下达建造指令的下一帧就会翻成 DONE，故该阈值远大于
        #: 任何正常情形，触发即代表确实卡死。
        self.running_abandon_sec = running_abandon_sec
        self.executor_llm: Optional[ExecutorLLM] = None
        # action_name -> (tag, time) short-term executor cache
        self._executor_cache: dict = {}

    # ------------------------------------------------------------------
    # plan management
    # ------------------------------------------------------------------
    def set_actions(self, pairs: List[Tuple[str, int]], mode: str = "replace") -> None:
        """Install a new ordered plan.

        :param pairs: ``[(action_name, quantity), ...]`` already ordered and with
                      supply depots injected.
        :param mode:  ``"replace"`` discards the unfinished plan; ``"append"``
                      keeps still-running/pending actions and adds these after.
        """
        new_actions = [
            PlannedAction.from_action_name(name, qty)
            for name, qty in pairs
        ]
        now = float(getattr(self.ai, "time", 0.0)) if getattr(self, "ai", None) else 0.0
        for pa in new_actions:
            pa.enqueue_time = now

        if mode == "append":
            kept = [a for a in self.actions if not a.is_terminal()]
            # ?????????????? PlannedAction ???????
            # ?? to_count???????????? docs ?????? ?3.2 + ?10.6??
            merged_new = []
            for pa in new_actions:
                merged = False
                if pa.category in (mapping.CAT_BUILD, mapping.CAT_ADDON):
                    for existing in kept:
                        if existing.action_name == pa.action_name and not existing.is_terminal():
                            existing.quantity += pa.quantity
                            if existing._act is not None:
                                if existing.category == mapping.CAT_BUILD and existing._act_target_count is not None:
                                    existing._act_target_count += pa.quantity
                                if hasattr(existing._act, 'to_count'):
                                    existing._act.to_count += pa.quantity
                            merged = True
                            break
                if not merged:
                    merged_new.append(pa)
            self.actions = kept + merged_new
        else:
            # replace ???????? action ? worker ???
            # ?? GridBuilding ??? SCV ??????? ?10.3??
            for old in self.actions:
                if getattr(old._act, "clear_worker", None):
                    try:
                        old._act.clear_worker()
                    except Exception:
                        pass
            self.actions = new_actions
        self._executor_cache.clear()
        logger.info("Scheduler installed %d actions (mode=%s)", len(new_actions), mode)

    def is_drained(self) -> bool:
        return all(a.is_terminal() for a in self.actions) if self.actions else True

    def is_single_waiter_remaining(self) -> bool:
        """True when exactly one non-terminal action remains and it is WAITING."""
        active = [a for a in self.actions if not a.is_terminal()]
        return len(active) == 1 and active[0].is_waiting()

    def is_drained_for_macro(self) -> bool:
        """Macro pipeline may advance when the queue is empty or only a waiter remains."""
        return self.is_drained() or self.is_single_waiter_remaining()

    def get_waiter(self) -> Optional[PlannedAction]:
        return self._get_waiter()

    def waiter_identity(self, pa: Optional[PlannedAction] = None) -> Optional[Tuple[str, float]]:
        """Stable key for deduplicating single-waiter prefetch."""
        target = pa if pa is not None else self._get_waiter()
        if target is None or not target.is_waiting():
            return None
        return (target.action_name, float(target.enqueue_time))

    def pending_summary_text(self, limit: int = 40) -> str:
        lines = []
        for a in self.actions:
            if a.is_terminal():
                continue
            lines.append(f"  - {a.short_label()} [{a.state}]")
            if len(lines) >= limit:
                break
        return "\n".join(lines) or "  (empty)"

    def _nonterminal_names(self, exclude: Optional[PlannedAction] = None) -> List[str]:
        return [a.action_name for a in self.actions if not a.is_terminal() and a is not exclude]

    def _waiting_summary(self, exclude: Optional[PlannedAction] = None) -> str:
        lines = [
            f"  - {a.short_label()} [{a.state}]"
            for a in self.actions
            if a.is_waiting() and a is not exclude
        ]
        return "\n".join(lines) or "  (none)"

    # ------------------------------------------------------------------
    # single-slot wait queue helpers
    # ------------------------------------------------------------------
    def _get_waiter(self) -> Optional[PlannedAction]:
        """Return the sole action allowed in the wait queue (first in list order)."""
        for pa in self.actions:
            if pa.is_waiting():
                return pa
        return None

    def _enforce_single_waiter(self) -> None:
        """Demote duplicate waiters so only the earliest non-terminal waiter remains."""
        keeper: Optional[PlannedAction] = None
        for pa in self.actions:
            if not pa.is_waiting():
                continue
            if keeper is None:
                keeper = pa
                continue
            pa.state = PENDING
            pa.wait_start_time = None
            pa.note = "pending: wait slot occupied"

    def _abandon_waiter_if_timed_out(self, waiter: Optional[PlannedAction], now: float) -> None:
        if waiter is None or self.wait_abandon_sec <= 0:
            return
        if waiter.wait_start_time is None:
            return
        if (now - waiter.wait_start_time) > self.wait_abandon_sec:
            waiter.state = ABANDONED
            waiter.note = "abandoned: waited too long"
            waiter.wait_start_time = None

    def _abandon_stuck_running(self, now: float) -> None:
        """放弃长时间卡在 RUNNING 的 build/research 动作。

        build/research 一旦成功下单（worker 下达建造指令）即翻为 DONE，因此
        RUNNING 长期不消失意味着 act 反复无法下单（典型如找不到落点）。放弃后
        队列得以 drain，macro 在下个周期会重新请求该结构（全新的 act + 干净的
        落点黑名单），从而自愈式重试，而不是永久阻塞。
        """
        if self.running_abandon_sec <= 0:
            return
        for pa in self.actions:
            if pa.state != RUNNING:
                continue
            if pa.category not in (mapping.CAT_BUILD, mapping.CAT_RESEARCH, mapping.CAT_ADDON):
                continue
            if pa.running_start_time is None:
                continue
            if (now - pa.running_start_time) > self.running_abandon_sec:
                # ????????? worker ????? SCV ????? Building ??
                # ??? ?10.4??
                if getattr(pa._act, "clear_worker", None):
                    try:
                        pa._act.clear_worker()
                    except Exception:
                        pass
                pa.state = ABANDONED
                pa.note = "abandoned: build stuck (no placement / cannot order)"
                pa.running_start_time = None
                logger.info("Abandoned stuck RUNNING action %s after %.0fs", pa.action_name, self.running_abandon_sec)

    def _waiter_reservation(self, waiter: Optional[PlannedAction]) -> Tuple[float, float, float]:
        if waiter is None or not waiter.is_waiting():
            return 0.0, 0.0, 0.0
        need_min, need_gas = self._live_cost(waiter)
        need_supply = self._live_supply_cost(waiter)
        return need_min, need_gas, need_supply

    def _claim_wait_slot(
        self,
        pa: PlannedAction,
        now: float,
        note: str,
        waiter: Optional[PlannedAction],
    ) -> Optional[PlannedAction]:
        """Enter wait on ``pa`` only if the single wait slot is free."""
        if waiter is not None and waiter is not pa:
            if pa.is_waiting():
                pa.state = PENDING
                pa.wait_start_time = None
                pa.note = "pending: wait slot occupied"
            return waiter
        self._enter_wait(pa, now, note)
        return pa

    # ------------------------------------------------------------------
    # main per-frame loop
    # ------------------------------------------------------------------
    async def execute(self) -> bool:
        if not self.actions:
            return True  # non-blocking: let background tactics run

        now = self.ai.time
        self._enforce_single_waiter()
        waiter = self._get_waiter()
        self._abandon_waiter_if_timed_out(waiter, now)
        self._abandon_stuck_running(now)
        waiter = self._get_waiter()

        reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(waiter)
        spent_min = 0.0
        spent_gas = 0.0
        spent_supply = 0.0

        index = 0
        while index < len(self.actions):
            pa = self.actions[index]
            index += 1

            if pa.is_terminal():
                continue

            waiter = self._get_waiter()
            reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(waiter)

            # 1) prerequisite / tech-chain gate
            try:
                available = is_available_now(self.ai, pa.action_name)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("prereq check failed for %s: %s", pa.action_name, exc)
                available = True

            if not available:
                if self._chain_in_progress_safe(pa):
                    waiter = self._claim_wait_slot(
                        pa, now, "waiting: prerequisite in progress", waiter
                    )
                else:
                    waiter = self._claim_wait_slot(
                        pa,
                        now,
                        "waiting: tech missing",
                        waiter,
                    )
                continue

            # 2) resource + supply gate (reservation-aware, with overtake for later actions)
            need_min, need_gas = self._live_cost(pa)
            need_supply = self._live_supply_cost(pa)
            avail_min = self.ai.minerals - reserved_min - spent_min
            avail_gas = self.ai.vespene - reserved_gas - spent_gas
            avail_supply = current_free_supply(self.ai) - reserved_supply - spent_supply

            if avail_min >= need_min and avail_gas >= need_gas and avail_supply >= need_supply:
                issued = await self._try_issue(pa, now)
                if issued:
                    spent_min += need_min
                    spent_gas += need_gas
                    spent_supply += need_supply
                elif pa.is_waiting():
                    waiter = self._claim_wait_slot(pa, now, pa.note, waiter)
            else:
                if pa.category == mapping.CAT_BUILD:
                    await self._premove_scv(pa)
                if waiter is None or waiter is pa:
                    if avail_min < need_min or avail_gas < need_gas:
                        note = "waiting: resources"
                    elif avail_supply < need_supply:
                        note = "waiting: supply"
                    else:
                        note = "waiting: resources"
                    waiter = self._claim_wait_slot(pa, now, note, waiter)
                    reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(waiter)

        return True

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _enter_wait(self, pa: PlannedAction, now: float, note: str) -> None:
        if pa.wait_start_time is None or not pa.is_waiting():
            pa.wait_start_time = now
        pa.state = WAITING
        pa.note = note
        pa.running_start_time = None

    def _chain_in_progress_safe(self, pa: PlannedAction) -> bool:
        try:
            return chain_in_progress(self.ai, pa.action_name)
        except Exception:
            return False

    async def _try_issue(self, pa: PlannedAction, now: float) -> bool:
        """Issue one step of ``pa``. Returns True if resources were committed."""
        # 挂件（TechLab/Reactor）也走 sharpy Act 路径：BuildAddon 会先检查右侧空位，
        # 避免建筑为了挂挂件而起飞（起飞的工厂/星港会破坏后续科技链前置）。
        if pa.category in (mapping.CAT_BUILD, mapping.CAT_RESEARCH, mapping.CAT_ADDON):
            return await self._issue_build_or_research(pa, now)
        return await self._issue_train_addon_morph(pa, now)

    # --- build / research via sharpy Acts -----------------------------
    async def _issue_build_or_research(self, pa: PlannedAction, now: float) -> bool:
        if pa._act is None and not pa._act_started:
            act = self._create_sharpy_act(pa)
            if act is None:
                pa.state = ABANDONED
                pa.note = "abandoned: cannot map to sharpy act"
                return False
            try:
                await act.start(self.knowledge)
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to start act for %s: %s", pa.action_name, exc)
                pa.state = ABANDONED
                pa.note = "abandoned: act start failed"
                return False
            pa._act = act
            pa._act_started = True
            # 记录本动作的目标结构数（含起始已有/在建），用于防重复放置闸。
            if pa.category == mapping.CAT_BUILD:
                pa._act_target_count = self._compute_build_to_count(pa)

        # 防重复放置：GridBuilding 依赖 already_pending 判断「是否已下单」，而该值
        # 对「SCV 刚接到建造指令、尚在赶路」有数帧延迟，导致同一个「建 1 座」的请求
        # 被连续下达、放出多座建筑（继而挤压、触发挂件 LIFT 飞行）。这里用无延迟的
        # 直接扫描（worker 携带的建造指令）来判定目标是否已满足，满足即收尾，不再让
        # act 重复下单。
        if pa.category == mapping.CAT_BUILD and pa._act_target_count is not None:
            unit_type = mapping.unit_type_for(pa.target_result or "")
            if unit_type is not None and self._existing_plus_en_route(unit_type) >= pa._act_target_count:
                # ????????????????????10.5 / 15.6??
                # ??????????????? DONE?fall through ? execute()?
                existing = self._equivalent_existing_count(unit_type)
                if existing >= pa._act_target_count:
                    pa.state = DONE
                    pa.issued_count = pa.quantity
                    pa.note = "done (build already in flight)"
                    pa.running_start_time = None
                    if getattr(pa._act, "clear_worker", None):
                        try:
                            pa._act.clear_worker()
                        except Exception:  # pragma: no cover
                            pass
                    return True
                # else: fall through to execute() for a more reliable check

        try:
            done = await pa._act.execute()
        except Exception as exc:  # pragma: no cover
            logger.debug("act.execute failed for %s: %s", pa.action_name, exc)
            done = False

        if done:
            # ?????GridBuilding / ??????????????? True?
            # ??????????????????? RUNNING?15.6??
            if (pa.category == mapping.CAT_BUILD
                    and pa._act_target_count is not None):
                unit_type = mapping.unit_type_for(pa.target_result or "")
                if unit_type is not None:
                    existing = self._equivalent_existing_count(unit_type)
                    if existing < pa._act_target_count:
                        pa.state = RUNNING
                        pa.wait_start_time = None
                        if pa.running_start_time is None:
                            pa.running_start_time = now
                        pa.note = "building/researching"
                        return True
            pa.state = DONE
            pa.issued_count = pa.quantity
            pa.note = "done"
            pa.running_start_time = None
            if (pa.category == mapping.CAT_BUILD
                    and hasattr(pa._act, "actual_placements")
                    and pa._act.actual_placements < pa.quantity):
                logger.warning(
                    "GridBuilding %s planned x%d but only placed %d building(s) "
                    "- possible premature DONE (see docs ??????).",
                    pa.action_name, pa.quantity, pa._act.actual_placements,
                )
        else:
            pa.state = RUNNING
            pa.wait_start_time = None
            if pa.running_start_time is None:
                pa.running_start_time = now
            pa.note = "building/researching"
        return True

    def _create_sharpy_act(self, pa: PlannedAction):
        if pa.category == mapping.CAT_RESEARCH:
            return mapping.make_research_act(pa.target_result)
        if pa.category == mapping.CAT_ADDON:
            return mapping.make_addon_act(pa.action_name, self._compute_addon_to_count(pa))
        to_count = self._compute_build_to_count(pa)
        return mapping.make_build_act(pa.action_name, pa.target_result, to_count)

    def _compute_addon_to_count(self, pa: PlannedAction) -> int:
        """Target count for an addon (current addons of that exact type + quantity)."""
        unit_type = mapping.unit_type_for(pa.target_result or "")
        if unit_type is None:
            parts = pa.action_name.upper().split("_")
            if len(parts) >= 3:
                unit_type = mapping.unit_type_for(parts[2] + parts[1])
        try:
            current = self.get_count(unit_type) if unit_type else 0
        except Exception:
            current = 0
        return int(current) + int(pa.quantity)

    def _compute_build_to_count(self, pa: PlannedAction) -> int:
        upper = pa.action_name.upper()
        try:
            if upper == "TERRANBUILD_COMMANDCENTER" or pa.target_result == "CommandCenter":
                current = self.ai.townhalls.amount
            elif "REFINERY" in upper:
                current = self.get_count(UnitTypeId.REFINERY)
            else:
                unit_type = mapping.unit_type_for(pa.target_result or "")
                current = self._equivalent_existing_count(unit_type) if unit_type else 0
        except Exception:
            current = 0
        return int(current) + int(pa.quantity)

    def _existing_plus_en_route(self, unit_type: UnitTypeId) -> int:
        """无延迟统计某结构「已有/在建 + 已有 SCV 正赶去建造」的总数。

        相较 ``already_pending``（对刚下达、SCV 尚在赶路的建造指令有数帧延迟），这里
        直接扫描 worker 携带的建造指令，可在下达当帧立即计入，从而避免同一请求被连续
        重复下单放出多座建筑。
        """
        try:
            existing = self._equivalent_existing_count(unit_type)
        except Exception:
            existing = 0
        try:
            creation_ability_id = self.ai._game_data.units[unit_type.value].creation_ability.id
        except Exception:
            return existing
        en_route = 0
        for worker in self.ai.workers:
            for order in worker.orders:
                if order.ability.id == creation_ability_id:
                    # ????????? double-count?sharpy cache ????
                    # worker order ???????????? ?10.5??
                    target = Point2.from_proto(order.target)
                    if not self.ai.structures.closer_than(1.0, target).exists:
                        en_route += 1
                    break
        return existing + en_route

    def _equivalent_existing_count(self, unit_type: UnitTypeId) -> int:
        existing = self.get_count(unit_type, include_pending=False, include_not_ready=True)
        flying_type = TERRAN_PRODUCTION_FLYING_EQUIVALENTS.get(unit_type)
        if flying_type is not None:
            existing += self.cache.own(flying_type).amount
        return existing

    # --- train / addon / morph via executor selection -----------------
    async def _issue_train_addon_morph(self, pa: PlannedAction, now: float) -> bool:
        if pa.ability is None:
            pa.state = ABANDONED
            pa.note = "abandoned: no ability id"
            return False

        candidates = await executor_select.candidate_executors(self.ai, pa.ability)
        if not candidates:
            self._enter_wait(pa, now, "waiting: no free producer")
            return False

        chosen = self._choose_executor(pa, candidates, now)
        if chosen is None:
            self._enter_wait(pa, now, "waiting: no executor chosen")
            return False

        try:
            chosen(pa.ability)
        except Exception as exc:  # pragma: no cover
            logger.debug("issue failed for %s on tag %s: %s", pa.action_name, chosen.tag, exc)
            self._enter_wait(pa, now, "waiting: issue failed")
            return False

        pa.issued_count += 1
        pa.wait_start_time = None
        if pa.issued_count >= pa.quantity:
            pa.state = DONE
            pa.note = "done"
        else:
            pa.state = RUNNING
            pa.note = f"issued {pa.issued_count}/{pa.quantity}"
        return True

    def _choose_executor(self, pa: PlannedAction, candidates, now: float):
        units_by_tag = {u.tag: u for u, _ in candidates}

        # single candidate -> rule pick, never call the LLM
        if len(candidates) == 1:
            return candidates[0][0]

        # short-term cache to avoid an LLM call every frame
        cached = self._executor_cache.get(pa.action_name)
        if cached and (now - cached[1]) < EXECUTOR_CACHE_SEC and cached[0] in units_by_tag:
            return units_by_tag[cached[0]]

        chosen_unit = None
        if self.executor_llm is not None:
            try:
                pending_names = self._nonterminal_names(exclude=pa)
                tag_aliases = executor_select.prompt_tag_aliases(candidates)
                tag_map = {}
                for real_tag, prompt_tag in tag_aliases.items():
                    if prompt_tag in tag_map:
                        tag_map = {}
                        logger.warning(
                            "Executor prompt tag collision for %s on tag%%%s; using rule fallback.",
                            pa.action_name,
                            1000,
                        )
                        break
                    tag_map[prompt_tag] = real_tag

                if tag_map:
                    tag = self.executor_llm(
                        ability_name=pa.action_name,
                        candidate_text=executor_select.candidates_text(candidates, tag_aliases=tag_aliases),
                        cost_hint=self._cost_hint(pa),
                        pending_summary=self.pending_summary_text(),
                        waiting_summary=self._waiting_summary(exclude=pa),
                        conflict_hints=executor_select.executor_conflict_hints(candidates, pending_names),
                        legal_tags=set(tag_map.keys()),
                        tag_map=tag_map,
                    )
                    if tag in units_by_tag:
                        chosen_unit = units_by_tag[tag]
            except Exception as exc:  # pragma: no cover
                logger.debug("executor LLM failed for %s: %s", pa.action_name, exc)

        if chosen_unit is None:
            # fallback: prefer idle, then first candidate
            idle = [u for u, _ in candidates if getattr(u, "is_idle", False)]
            chosen_unit = idle[0] if idle else candidates[0][0]

        self._executor_cache[pa.action_name] = (chosen_unit.tag, now)
        return chosen_unit

    def _live_cost(self, pa: PlannedAction) -> Tuple[float, float]:
        """Real minerals/gas cost of issuing ``pa`` right now (DB cost fallback)."""
        if pa.ability is not None:
            try:
                cost = self.ai.calculate_cost(pa.ability)
                return float(cost.minerals), float(cost.vespene)
            except Exception:
                pass
        return float(pa.cost_minerals), float(pa.cost_gas)

    def _live_supply_cost(self, pa: PlannedAction) -> float:
        """Supply cost of issuing ``pa`` right now (DB cost fallback)."""
        if pa.ability is not None:
            try:
                cost = self.ai.calculate_cost(pa.ability)
                supply = float(getattr(cost, "supply", 0) or 0)
                if supply > 0:
                    return supply
            except Exception:
                pass
        return max(0.0, float(pa.cost_supply))

    def _cost_hint(self, pa: PlannedAction) -> str:
        seconds = pa.cost_time_frames / FRAMES_PER_SECOND if pa.cost_time_frames else 0.0
        return (
            f"minerals {pa.cost_minerals}, gas {pa.cost_gas}, "
            f"supply {pa.cost_supply}, ~{seconds:.0f}s"
        )

    # --- SCV pre-move for waiting build actions -----------------------
    async def _premove_scv(self, pa: PlannedAction) -> None:
        unit_type = mapping.unit_type_for(pa.target_result or "")
        if unit_type is None:
            return
        try:
            if pa._premove_position is None:
                near = self._main_build_anchor()
                pos = await self.ai.find_placement(unit_type, near, max_distance=20, placement_step=2)
                if pos is None:
                    return
                pa._premove_position = pos
            position: Point2 = pa._premove_position
            worker = self.get_worker_builder(position, pa._premove_worker_tag)
            if worker is None:
                return
            pa._premove_worker_tag = worker.tag
            if worker.distance_to(position) > 3 and not self.has_build_order(worker):
                worker.move(position)
        except Exception as exc:  # pragma: no cover
            logger.debug("premove failed for %s: %s", pa.action_name, exc)

    def _main_build_anchor(self) -> Point2:
        try:
            if self.ai.townhalls.exists:
                return self.ai.townhalls.first.position
        except Exception:
            pass
        return self.ai.start_location
