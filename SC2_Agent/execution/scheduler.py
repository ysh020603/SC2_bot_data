"""Command-style execution scheduler (replaces the declarative ActLLMOngoingTasks).

``ExecutionScheduler`` is a sharpy ``ActBase`` driven every frame. It walks an
ordered list of :class:`PlannedAction` plus a separate single waiter slot:

* **independent waiter slot** (``self.waiter``): at most one ``PlannedAction``
  is held in ``WAITING`` outside of ``self.actions``. New step actions appended
  via :py:meth:`set_actions` never disturb the waiter; it persists until issued
  or abandoned. The slot is consulted *first* every frame so that a waiter
  whose resources/tech are now satisfied is fired immediately.
* **three-tier priority scan** based on ``cost.supply`` sign:

  - **P0** (``cost.supply < 0``): supply-providing actions
    (e.g. ``TERRANBUILD_SUPPLYDEPOT``, ``TERRANBUILD_COMMANDCENTER``).
  - **P1** (``cost.supply == 0``): supply-neutral actions
    (most builds, addons, upgrades, ``BuildGas``).
  - **P2** (``cost.supply > 0``): supply-consuming train/morph actions.

  The scan visits priority tiers in ascending numeric order. A lower tier is
  scanned only when the higher tier has no PA blocked (``PENDING``) and the
  current waiter, if any, is not from a higher tier. Within a tier we keep
  the original *resource reservation + overtake* semantics: actions later in
  the list may execute as long as they do not consume the waiter's reserved
  minerals / gas / supply.
* **prerequisite / tech-chain checks** via ``data_tools.prereq_runtime`` (obs
  three-state aware): blocked actions become the waiter (or remain
  ``PENDING`` when the slot is taken); missing prerequisites are not inserted
  automatically.
* **execution split**: ``train`` may use the executor LLM after rule candidate
  filtering; ``morph`` uses rule selection directly; ``build/research/addon``
  delegate to a lazily-created sharpy Act or to :class:`DirectBuildExecutor` for
  hand-managed Terran structures.
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
from SC2_Agent.execution.direct_build import (
    DIRECT_TERRAN_BUILD_TYPES,
    DirectBuildExecutor,
    direct_build_unit_type,
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

TERRAN_BUILDING_EQUIVALENTS = {
    UnitTypeId.SUPPLYDEPOT: (
        UnitTypeId.SUPPLYDEPOTLOWERED,
        UnitTypeId.SUPPLYDEPOTDROP,
    ),
    UnitTypeId.COMMANDCENTER: (
        UnitTypeId.COMMANDCENTERFLYING,
        UnitTypeId.ORBITALCOMMAND,
        UnitTypeId.ORBITALCOMMANDFLYING,
        UnitTypeId.PLANETARYFORTRESS,
    ),
    UnitTypeId.ORBITALCOMMAND: (
        UnitTypeId.ORBITALCOMMANDFLYING,
    ),
}

TERRAN_DEFER_APPEND_BUILD_TYPES = DIRECT_TERRAN_BUILD_TYPES
TERRAN_STICKY_BUILD_ACTIONS = {
    "TERRANBUILD_COMMANDCENTER",
}
TERRAN_MORPH_SOURCE_TARGETS = {
    "UPGRADETOORBITAL_ORBITALCOMMAND": (
        UnitTypeId.COMMANDCENTER,
        UnitTypeId.ORBITALCOMMAND,
    ),
    "UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS": (
        UnitTypeId.COMMANDCENTER,
        UnitTypeId.PLANETARYFORTRESS,
    ),
}


#: Type of the executor-LLM callback the bot injects.
#: (ability_name, candidate_text, cost_hint, pending_summary,
#:  waiting_summary, conflict_hints, legal_tags, tag_map) -> Optional[int]
ExecutorLLM = Callable[..., Optional[int]]


#: Priority tiers (lower number = higher priority).
P0_SUPPLY_PROVIDER = 0
P1_SUPPLY_NEUTRAL = 1
P2_SUPPLY_CONSUMER = 2

PRIORITY_TIERS = (P0_SUPPLY_PROVIDER, P1_SUPPLY_NEUTRAL, P2_SUPPLY_CONSUMER)


def _priority_for(pa: "PlannedAction") -> int:
    """Classify a PA by its ``cost.supply`` sign (no DB lookup).

    Delegates to :py:meth:`PlannedAction.priority_tier` so the two views agree.
    """
    return pa.priority_tier()


class ExecutionScheduler(ActBase):
    def __init__(self, wait_abandon_sec: float = 20.0, running_abandon_sec: float = 25.0):
        super().__init__()
        #: Ordered list of PAs not currently in the waiter slot. Invariant:
        #: no PA in this list is ever in ``WAITING``; ``WAITING`` lives in
        #: :attr:`waiter` exclusively.
        self.actions: List[PlannedAction] = []
        #: The single ``WAITING`` action, kept outside ``self.actions`` so
        #: that step transitions appending to the list cannot disturb it.
        self.waiter: Optional[PlannedAction] = None
        self.wait_abandon_sec = wait_abandon_sec
        #: build/research 动作在 RUNNING 状态停留超过该秒数仍未下单成功（例如
        #: 找不到落点导致 act 反复返回 False）即放弃，避免单个动作永久阻塞 macro。
        #: 正常 build 在 worker 下达建造指令的下一帧就会翻成 DONE，故该阈值远大于
        #: 任何正常情形，触发即代表确实卡死。
        self.running_abandon_sec = running_abandon_sec
        self.executor_llm: Optional[ExecutorLLM] = None
        # action_name -> (tag, time) short-term executor cache
        self._executor_cache: dict = {}
        self._direct_build_executor: Optional[DirectBuildExecutor] = None

    # ------------------------------------------------------------------
    # plan management
    # ------------------------------------------------------------------
    def set_actions(self, pairs: List[Tuple[str, int]], mode: str = "replace") -> None:
        """Install a new ordered plan.

        The waiter slot (``self.waiter``) is **never modified** by this call.
        Same-name merging in ``append`` mode only considers PAs in
        ``self.actions``; new appended PAs that happen to share a name with the
        waiter are kept as their own PA in the list to avoid silently bumping
        the waiter's quantity.

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
            # 同名 build/addon 直接合并到 list 中现存的同名 PA（不波及 waiter）。
            # 详细动机参见 docs/系统文档.md §3.2 与 §10.6。
            merged_new = []
            deferred_new = 0
            for pa in new_actions:
                if self._defer_append_build_if_needed(pa, kept, now):
                    deferred_new += 1
                    merged_new.append(pa)
                    continue
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
                            if existing._direct_build_target_count is not None:
                                existing._direct_build_target_count += pa.quantity
                                if existing._direct_build_helper is not None:
                                    existing._direct_build_helper.to_count = existing._direct_build_target_count
                            merged = True
                            break
                if not merged:
                    merged_new.append(pa)
            self.actions = kept + merged_new
            if deferred_new:
                logger.info(
                    "Scheduler append deferred %d build action(s)", deferred_new
                )
        else:
            carried = self._carry_replace_actions(new_actions)
            # replace 模式下，列表中其他未完成 act 的 worker 必须解锁（避免该 SCV
            # 被锁死在已废弃的 Building 任务上，参见 docs §10.3）。
            for old in self.actions:
                if old in carried:
                    continue
                if old._act is not None and getattr(old._act, "clear_worker", None):
                    try:
                        old._act.clear_worker()
                    except Exception:
                        pass
                self._clear_direct_build_worker(old)
            self.actions = carried + new_actions
            # waiter 单独处理：仅当其为 sticky build 且新计划未覆盖时保留；
            # 否则也作为 stale 清理（释放 worker 与槽位）。
            if self.waiter is not None:
                new_names = {a.action_name for a in new_actions}
                keep_waiter = (
                    self._is_sticky_build_action(self.waiter)
                    and self.waiter.action_name not in new_names
                )
                if not keep_waiter:
                    stale = self.waiter
                    if stale._act is not None and getattr(stale._act, "clear_worker", None):
                        try:
                            stale._act.clear_worker()
                        except Exception:
                            pass
                    self._clear_direct_build_worker(stale)
                    self.waiter = None
            if carried:
                self._emit_status(
                    "replace carried %d unfinished strategic action(s): %s",
                    len(carried),
                    ", ".join(a.short_label() for a in carried),
                )
        self._executor_cache.clear()
        logger.info("Scheduler installed %d actions (mode=%s)", len(new_actions), mode)

    def is_drained(self) -> bool:
        """True when both the action list and waiter slot are fully terminal."""
        if self.waiter is not None and not self.waiter.is_terminal():
            return False
        return all(a.is_terminal() for a in self.actions) if self.actions else True

    def has_no_executable_actions(self) -> bool:
        """True when no non-terminal, non-deferred action remains in the list.

        The waiter slot counts as executable work: if a PA is waiting for
        resources, supply, tech, or a producer, the current strategic step is
        not drained yet.
        """
        if self.waiter is not None and not self.waiter.is_terminal():
            return False
        for a in self.actions:
            if a.is_terminal():
                continue
            if getattr(a, '_defer_until_build_type', None) is not None:
                continue
            return False
        return True

    def is_drained_for_macro(self) -> bool:
        """Macro pipeline may advance when no executable actions remain."""
        return self.is_drained() or self.has_no_executable_actions()

    def get_waiter(self) -> Optional[PlannedAction]:
        return self.waiter

    def all_planned_actions(self) -> List[PlannedAction]:
        """Iterable view over both the action list and the waiter slot."""
        if self.waiter is None:
            return list(self.actions)
        return list(self.actions) + [self.waiter]

    def pending_summary_text(self, limit: int = 40) -> str:
        lines = []
        if self.waiter is not None and not self.waiter.is_terminal():
            lines.append(f"  * {self.waiter.short_label()} [{self.waiter.state}] (waiter)")
        for a in self.actions:
            if a.is_terminal():
                continue
            lines.append(f"  - {a.short_label()} [{a.state}]")
            if len(lines) >= limit:
                break
        return "\n".join(lines) or "  (empty)"

    def _nonterminal_names(self, exclude: Optional[PlannedAction] = None) -> List[str]:
        names = [a.action_name for a in self.actions if not a.is_terminal() and a is not exclude]
        if (
            self.waiter is not None
            and not self.waiter.is_terminal()
            and self.waiter is not exclude
        ):
            names.append(self.waiter.action_name)
        return names

    def _waiting_summary(self, exclude: Optional[PlannedAction] = None) -> str:
        if (
            self.waiter is not None
            and self.waiter.is_waiting()
            and self.waiter is not exclude
        ):
            return f"  - {self.waiter.short_label()} [{self.waiter.state}]"
        return "  (none)"

    def _is_sticky_build_action(self, pa: PlannedAction) -> bool:
        return (
            pa.category == mapping.CAT_BUILD
            and pa.action_name.upper() in TERRAN_STICKY_BUILD_ACTIONS
            and not self._build_action_satisfied(pa)
        )

    def _build_action_satisfied(self, pa: PlannedAction) -> bool:
        unit_type = mapping.unit_type_for(pa.target_result or "")
        if unit_type is None:
            return False
        target_count = pa._direct_build_target_count or pa._act_target_count
        if target_count is None:
            return False
        return self._build_progress_count(unit_type) >= int(target_count)

    def _mark_done_if_already_satisfied(self, pa: PlannedAction, now: float) -> bool:
        """Close stale PAs whose target result is already present.

        This is intentionally generic: old addon/research/morph/build PAs can be
        satisfied by another independent PA, by an in-progress order finishing,
        or by the game state changing while they sit in the waiter slot.
        """
        if pa.is_terminal():
            return True
        if self._can_use_direct_build(pa):
            return False

        satisfied = False
        if pa.category == mapping.CAT_BUILD:
            satisfied = self._build_action_satisfied(pa)
        elif pa.category == mapping.CAT_ADDON:
            satisfied = self._addon_action_satisfied(pa)
        elif pa.category == mapping.CAT_RESEARCH:
            satisfied = self._research_action_satisfied(pa)
        elif pa.category == mapping.CAT_MORPH:
            satisfied = self._morph_action_satisfied(pa)

        if not satisfied:
            return False

        pa.state = DONE
        pa.issued_count = max(int(pa.issued_count), int(pa.quantity))
        pa.note = "done (already satisfied)"
        pa.wait_start_time = None
        pa.running_start_time = None
        self._emit_status(
            "PA %s DONE: target already satisfied",
            pa.action_name,
        )
        return True

    def _mark_satisfied_actions(self, now: float) -> None:
        for pa in list(self.actions):
            self._mark_done_if_already_satisfied(pa, now)
        if self.waiter is not None and self._mark_done_if_already_satisfied(self.waiter, now):
            self._release_waiter_back_to_actions(self.waiter)

    def _addon_action_satisfied(self, pa: PlannedAction) -> bool:
        unit_type = self._addon_unit_type(pa)
        if unit_type is None:
            return False

        target_count = None
        if pa._act is not None:
            target_count = getattr(pa._act, "to_count", None)
        if target_count is None:
            target_count = getattr(pa, "_addon_target_count", None)
        if target_count is None:
            return False

        return self._equivalent_existing_count(unit_type) >= int(target_count)

    def _addon_unit_type(self, pa: PlannedAction) -> Optional[UnitTypeId]:
        unit_type = mapping.unit_type_for(pa.target_result or "")
        if unit_type is not None:
            return unit_type
        parts = pa.action_name.upper().split("_")
        if len(parts) >= 3:
            return mapping.unit_type_for(parts[2] + parts[1])
        return None

    def _research_action_satisfied(self, pa: PlannedAction) -> bool:
        upgrade = mapping.upgrade_for(pa.target_result or "")
        if upgrade is None:
            return False
        try:
            return upgrade in self.ai.state.upgrades
        except Exception:
            return False

    def _morph_action_satisfied(self, pa: PlannedAction) -> bool:
        source_target = TERRAN_MORPH_SOURCE_TARGETS.get(pa.action_name.upper())
        if source_target is None:
            return False
        _source_type, target_type = source_target
        target_count = getattr(pa, "_morph_target_total", None)
        if target_count is None:
            return False
        try:
            satisfied_total = self._equivalent_existing_count(target_type) + self._morph_order_count(pa)
        except Exception:
            return False
        return int(satisfied_total) >= int(target_count)

    def _carry_replace_actions(self, new_actions: List[PlannedAction]) -> List[PlannedAction]:
        carried: List[PlannedAction] = []
        new_names = {a.action_name for a in new_actions}
        for old in self.actions:
            if old.is_terminal():
                continue
            if not self._is_sticky_build_action(old):
                continue
            if old.action_name in new_names:
                continue
            carried.append(old)
        return carried

    # ------------------------------------------------------------------
    # waiter-slot helpers
    # ------------------------------------------------------------------
    def _enforce_single_waiter(self) -> None:
        """Defensive cleanup: keep ``self.actions`` free of ``WAITING`` PAs.

        Should rarely trigger because :py:meth:`_claim_wait_slot` and
        :py:meth:`_release_waiter_back_to_actions` maintain the invariant,
        but acts as a safety net against external state mutations.
        """
        for pa in list(self.actions):
            if not pa.is_waiting():
                continue
            if self.waiter is None:
                self.actions.remove(pa)
                self.waiter = pa
            else:
                pa.state = PENDING
                pa.wait_start_time = None
                pa.note = "pending: wait slot occupied"

    def _abandon_waiter_if_timed_out(self, waiter: Optional[PlannedAction], now: float) -> None:
        if waiter is None or self.wait_abandon_sec <= 0:
            return
        if waiter.wait_start_time is None:
            return
        if (now - waiter.wait_start_time) > self.wait_abandon_sec:
            if self._is_sticky_build_action(waiter):
                waiter.wait_start_time = now
                waiter.note = "waiting: strategic build retained"
                self._emit_status(
                    "Retained WAITING strategic action %s after %.0fs",
                    waiter.action_name,
                    self.wait_abandon_sec,
                )
                return
            if self._can_use_direct_build(waiter):
                try:
                    if self._get_direct_build_executor().keep_waiting_if_progressing(waiter, now):
                        return
                except Exception:
                    pass
            waiter.state = ABANDONED
            waiter.note = "abandoned: waited too long"
            waiter.wait_start_time = None
            if self.waiter is waiter:
                self.waiter = None
            # 静默 ABANDON 之前是隐形的，回归测试时极难定位——必须留下日志
            # 才能区分「PA 是被 wait 超时干掉的」vs「stuck running 干掉的」
            # vs「正常 DONE」。
            self._emit_status(
                "Abandoned WAITING action %s after %.0fs (wait_abandon_sec)",
                waiter.action_name, self.wait_abandon_sec,
            )

    def _abandon_stuck_running(self, now: float) -> None:
        """放弃长时间卡在 RUNNING 的 build/research 动作。

        build/research 一旦成功下单（worker 下达建造指令）即翻为 DONE，因此
        RUNNING 长期不消失意味着 act 反复无法下单（典型如找不到落点）。放弃后
        队列得以 drain，macro 在下个周期会重新请求该结构（全新的 act + 干净的
        落点黑名单），从而自愈式重试，而不是永久阻塞。
        """
        if self.running_abandon_sec <= 0:
            return
        for pa in list(self.actions):
            if pa.state != RUNNING:
                continue
            if pa.category not in (mapping.CAT_BUILD, mapping.CAT_RESEARCH, mapping.CAT_ADDON):
                continue
            if pa.running_start_time is None:
                continue
            if (now - pa.running_start_time) > self.running_abandon_sec:
                if self._is_sticky_build_action(pa):
                    # sticky build: 转回 WAITING 等下次资源/落点机会，需要走
                    # claim_wait_slot 才能进独立 waiter 槽（preempt 当前槽内
                    # 任何同档非 sticky 占用者）。
                    self._claim_wait_slot(pa, now, "waiting: strategic build retry", preempt=True)
                    pa.running_start_time = None
                    continue
                # 清掉与本动作绑定的 worker，避免 SCV 一直停留在已废弃的 Building 任务上
                # （详见 docs §10.4）。
                if pa._act is not None and getattr(pa._act, "clear_worker", None):
                    try:
                        pa._act.clear_worker()
                    except Exception:
                        pass
                self._clear_direct_build_worker(pa)
                pa.state = ABANDONED
                pa.note = "abandoned: build stuck (no placement / cannot order)"
                pa.running_start_time = None
                self._emit_status(
                    "Abandoned stuck RUNNING action %s after %.0fs",
                    pa.action_name,
                    self.running_abandon_sec,
                )

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
        *,
        preempt: bool = False,
    ) -> bool:
        """Promote ``pa`` to the waiter slot.

        Behavior:

        * If the slot is empty, ``pa`` becomes the waiter.
        * If ``pa`` already is the waiter, just refresh state/note.
        * Otherwise the slot is held by another PA; ``pa`` may displace it
          when ``preempt`` is True or when ``pa`` has a strictly higher
          priority (lower numeric tier). The displaced PA is pushed back to
          the action list as ``PENDING``.
        * If preemption is not allowed, ``pa`` stays where it is (in
          ``self.actions``) marked ``PENDING`` with note "pending: wait slot
          occupied". Returns ``False``.

        Returns ``True`` iff ``pa`` is now the waiter.
        """
        if self.waiter is pa:
            self._enter_wait(pa, now, note)
            return True

        if self.waiter is None:
            self._move_to_waiter(pa, now, note)
            return True

        new_prio = _priority_for(pa)
        cur_prio = _priority_for(self.waiter)
        can_preempt = preempt or new_prio < cur_prio
        if not can_preempt:
            if pa.is_waiting():
                pa.state = PENDING
                pa.wait_start_time = None
                pa.note = "pending: wait slot occupied"
            return False

        old = self.waiter
        old.state = PENDING
        old.wait_start_time = None
        old.note = "pending: preempted by higher-priority action"
        self.waiter = None
        if old not in self.actions:
            self.actions.append(old)
        self._move_to_waiter(pa, now, note)
        return True

    def _move_to_waiter(self, pa: PlannedAction, now: float, note: str) -> None:
        """Internal: move ``pa`` from ``self.actions`` (if present) to the slot."""
        try:
            self.actions.remove(pa)
        except ValueError:
            pass
        self._enter_wait(pa, now, note)
        self.waiter = pa

    def _release_waiter_back_to_actions(self, pa: PlannedAction) -> None:
        """Move ``pa`` out of the waiter slot back to ``self.actions``.

        Called once :py:meth:`_try_issue` (or DONE detection) has advanced
        the PA out of ``WAITING``. Done/abandoned PAs are still re-appended
        so that any downstream introspection (logs, ``pending_summary_text``)
        can see them; the next frame's terminal-skip filters will ignore them.
        """
        if self.waiter is pa:
            self.waiter = None
        if pa not in self.actions:
            self.actions.append(pa)

    # ------------------------------------------------------------------
    # main per-frame loop
    # ------------------------------------------------------------------
    async def execute(self) -> bool:
        if not self.actions and self.waiter is None:
            return True  # non-blocking: let background tactics run

        now = self.ai.time

        # 1) housekeeping: defensive single-waiter invariant + timeout abandons.
        self._enforce_single_waiter()
        self._mark_satisfied_actions(now)
        self._abandon_waiter_if_timed_out(self.waiter, now)
        self._abandon_stuck_running(now)

        spent = {"min": 0.0, "gas": 0.0, "supply": 0.0}
        # PAs that have already been issued via the waiter path this frame.
        # Prevents the same PA being re-issued by the priority scan after it
        # has been released back into ``self.actions``.
        issued_this_frame: set = set()

        # 2) try to fire the current waiter first - if its tech / resources are
        #    now satisfied, do not make it wait another frame.
        if self.waiter is not None:
            await self._try_issue_waiter(now, spent, issued_this_frame)

        # 3) priority-tier scan. Stop descending tiers as soon as a higher tier
        #    has any blocked / waiting PA, so lower-priority actions cannot
        #    overtake an unsatisfied higher-priority demand.
        for prio in PRIORITY_TIERS:
            if self.waiter is not None and _priority_for(self.waiter) < prio:
                break
            blocked = await self._scan_priority_group(prio, now, spent, issued_this_frame)
            if blocked:
                break

        return True

    async def _try_issue_waiter(self, now: float, spent: dict, issued_this_frame: set) -> None:
        """If the current waiter can issue this frame, do it and free the slot."""
        pa = self.waiter
        if pa is None or pa.is_terminal():
            return

        # tech / prerequisite gate
        try:
            available = is_available_now(self.ai, pa.action_name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("prereq check failed for %s: %s", pa.action_name, exc)
            available = True
        if not available:
            note = (
                "waiting: prerequisite in progress"
                if self._chain_in_progress_safe(pa)
                else "waiting: tech missing"
            )
            self._enter_wait(pa, now, note)
            return

        if self._mark_done_if_already_satisfied(pa, now):
            self._release_waiter_back_to_actions(pa)
            return

        # direct build "already satisfied" shortcut (sets state DONE in place)
        if await self._direct_build_done_before_resource_gate(pa, now):
            self._release_waiter_back_to_actions(pa)
            return

        # resource gate (waiter does not reserve against itself)
        need_min, need_gas = self._live_cost(pa)
        need_supply = self._live_supply_cost(pa)
        avail_min = self.ai.minerals - spent["min"]
        avail_gas = self.ai.vespene - spent["gas"]
        avail_supply = current_free_supply(self.ai) - spent["supply"]
        if avail_min < need_min or avail_gas < need_gas or avail_supply < need_supply:
            if avail_min < need_min or avail_gas < need_gas:
                note = "waiting: resources"
            else:
                note = "waiting: supply"
            self._enter_wait(pa, now, note)
            return

        issued = await self._try_issue(pa, now)
        if issued:
            spent["min"] += need_min
            spent["gas"] += need_gas
            spent["supply"] += need_supply
            issued_this_frame.add(id(pa))
            if pa.is_waiting():
                # _try_issue may have re-marked WAITING (e.g. multi-quantity build
                # without progress); leave in slot to retry next frame.
                return
            self._release_waiter_back_to_actions(pa)
        elif pa.is_waiting():
            # remained waiting (e.g. no free producer); keep in slot
            return

    async def _scan_priority_group(
        self, prio: int, now: float, spent: dict, issued_this_frame: set
    ) -> bool:
        """Scan all PAs of ``prio`` in ``self.actions``.

        Within the tier we keep the original *resource reservation + overtake*
        logic: the waiter's cost is reserved out of available funds, but later
        actions that fit the leftover may still execute.

        Returns ``True`` if the tier has any non-issued (PENDING / WAITING /
        deferred-not-released) PA after the scan. The caller uses this to
        decide whether to stop descending into lower-priority tiers.
        """
        # Recompute reservation each iteration: waiter may have changed.
        reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(self.waiter)

        for pa in list(self.actions):
            if pa.is_terminal():
                continue
            if _priority_for(pa) != prio:
                continue
            if id(pa) in issued_this_frame:
                continue
            if not self._release_or_skip_deferred(pa):
                continue

            # 1) prerequisite / tech gate
            try:
                available = is_available_now(self.ai, pa.action_name)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("prereq check failed for %s: %s", pa.action_name, exc)
                available = True
            if not available:
                note = (
                    "waiting: prerequisite in progress"
                    if self._chain_in_progress_safe(pa)
                    else "waiting: tech missing"
                )
                self._claim_wait_slot(pa, now, note)
                reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(self.waiter)
                continue

            if self._mark_done_if_already_satisfied(pa, now):
                continue

            if await self._direct_build_done_before_resource_gate(pa, now):
                # may have transitioned to DONE in place; nothing else to do.
                continue

            # 2) resource + supply gate
            need_min, need_gas = self._live_cost(pa)
            need_supply = self._live_supply_cost(pa)
            avail_min = self.ai.minerals - reserved_min - spent["min"]
            avail_gas = self.ai.vespene - reserved_gas - spent["gas"]
            avail_supply = current_free_supply(self.ai) - reserved_supply - spent["supply"]

            if avail_min >= need_min and avail_gas >= need_gas and avail_supply >= need_supply:
                issued = await self._try_issue(pa, now)
                if issued:
                    spent["min"] += need_min
                    spent["gas"] += need_gas
                    spent["supply"] += need_supply
                    issued_this_frame.add(id(pa))
                    if pa.is_waiting():
                        # try_issue ended up in WAITING (e.g. no producer); promote.
                        self._claim_wait_slot(pa, now, pa.note)
                        reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(self.waiter)
                elif pa.is_waiting():
                    self._claim_wait_slot(pa, now, pa.note)
                    reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(self.waiter)
            else:
                # not enough resources; promote to waiter (or stay PENDING when slot taken).
                if avail_min < need_min or avail_gas < need_gas:
                    note = "waiting: resources"
                elif avail_supply < need_supply:
                    note = "waiting: supply"
                else:
                    note = "waiting: resources"
                preempt = self._is_sticky_build_action(pa)
                if self._claim_wait_slot(pa, now, note, preempt=preempt):
                    reserved_min, reserved_gas, reserved_supply = self._waiter_reservation(self.waiter)

        # Determine whether this tier still has unsatisfied work.
        if self.waiter is not None and _priority_for(self.waiter) == prio:
            return True
        for pa in self.actions:
            if pa.is_terminal():
                continue
            if _priority_for(pa) != prio:
                continue
            if getattr(pa, "_defer_until_build_type", None) is not None:
                continue
            if pa.state == PENDING:
                return True
        return False

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

    def _can_use_direct_build(self, pa: PlannedAction) -> bool:
        return direct_build_unit_type(pa) is not None

    def _get_direct_build_executor(self) -> DirectBuildExecutor:
        if self._direct_build_executor is None:
            self._direct_build_executor = DirectBuildExecutor(self)
        return self._direct_build_executor

    async def _direct_build_done_before_resource_gate(self, pa: PlannedAction, now: float) -> bool:
        if not self._can_use_direct_build(pa):
            return False
        return await self._get_direct_build_executor().mark_done_if_satisfied(pa, now)

    def _clear_direct_build_worker(self, pa: PlannedAction) -> None:
        helper = getattr(pa, "_direct_build_helper", None)
        if helper is not None and getattr(helper, "clear_worker", None):
            try:
                helper.clear_worker()
            except Exception:
                pass
        pa._direct_build_worker_tag = None

    def _defer_append_build_if_needed(
        self,
        pa: PlannedAction,
        kept: List[PlannedAction],
        now: float,
    ) -> bool:
        """Keep future build demand queued without merging it into an active batch."""
        if pa.category != mapping.CAT_BUILD:
            return False
        unit_type = mapping.unit_type_for(pa.target_result or "")
        if unit_type not in TERRAN_DEFER_APPEND_BUILD_TYPES:
            return False

        # Active same-name PAs may live either in the kept list or in the waiter slot.
        candidates: List[PlannedAction] = list(kept)
        if self.waiter is not None and not self.waiter.is_terminal():
            candidates.append(self.waiter)

        for existing in candidates:
            if existing is pa:
                continue
            if existing.action_name != pa.action_name:
                continue
            if existing.is_terminal():
                continue
            self._mark_deferred_build(
                pa,
                unit_type,
                now,
                "same build action is still %s" % existing.state,
            )
            self._emit_status(
                "append deferred %s x%d: same build action is still %s",
                pa.action_name,
                pa.quantity,
                existing.state,
            )
            return True

        if self._field_build_in_flight(unit_type):
            self._mark_deferred_build(
                pa,
                unit_type,
                now,
                "%s already in flight" % unit_type.name,
            )
            self._emit_status(
                "append deferred %s x%d: %s already in flight",
                pa.action_name,
                pa.quantity,
                unit_type.name,
            )
            return True

        return False

    def _mark_deferred_build(
        self,
        pa: PlannedAction,
        unit_type: UnitTypeId,
        now: float,
        reason: str,
    ) -> None:
        pa._defer_until_build_type = unit_type
        pa._defer_reason = reason
        pa._defer_created_time = now
        pa.state = PENDING
        pa.note = "deferred: " + reason
        pa.wait_start_time = None
        pa.running_start_time = None

    def _release_or_skip_deferred(self, pa: PlannedAction) -> bool:
        unit_type = getattr(pa, "_defer_until_build_type", None)
        if unit_type is None:
            return True

        if self._has_active_same_build_action(pa):
            pa.note = "deferred: same build action still active"
            return False

        if self._field_build_in_flight(unit_type):
            pa.note = "deferred: %s already in flight" % unit_type.name
            return False

        self._emit_status(
            "append released %s x%d: %s is no longer in flight",
            pa.action_name,
            pa.quantity,
            unit_type.name,
        )
        pa._defer_until_build_type = None
        pa._defer_reason = ""
        pa._defer_created_time = None
        pa.note = "pending: deferred build released"
        pa.state = PENDING
        return True

    def _has_active_same_build_action(self, pa: PlannedAction) -> bool:
        # Waiter shares the same defer-prevention semantics as list-resident actions.
        if (
            self.waiter is not None
            and self.waiter is not pa
            and not self.waiter.is_terminal()
            and getattr(self.waiter, "_defer_until_build_type", None) is None
            and self.waiter.action_name == pa.action_name
        ):
            return True
        for other in self.actions:
            if other is pa:
                return False
            if other.is_terminal():
                continue
            if getattr(other, "_defer_until_build_type", None) is not None:
                continue
            if other.action_name == pa.action_name:
                return True
        return False

    def _field_build_in_flight(self, unit_type: UnitTypeId) -> bool:
        try:
            for structure in self.ai.structures:
                if structure.type_id != unit_type:
                    continue
                if getattr(structure, "build_progress", 1.0) < 1.0:
                    return True
        except Exception:
            pass

        return bool(self._worker_build_order_positions_without_structure(unit_type))

    def _worker_build_order_positions_without_structure(self, unit_type: UnitTypeId) -> List[Point2]:
        try:
            creation_ability_id = self.ai._game_data.units[unit_type.value].creation_ability.id
        except Exception:
            return []

        positions: List[Point2] = []
        try:
            workers = self.ai.workers
        except Exception:
            return positions

        for worker in workers:
            for order in worker.orders:
                if order.ability.id != creation_ability_id:
                    continue
                try:
                    target = Point2.from_proto(order.target)
                except Exception:
                    break
                try:
                    if not self.ai.structures.closer_than(1.0, target).exists:
                        positions.append(target)
                except Exception:
                    positions.append(target)
                break
        return positions

    def _emit_status(self, message: str, *args) -> None:
        text = message % args if args else message
        logger.info(text)
        try:
            self.knowledge.print(f"[Scheduler] {text}", stats=False)
        except Exception:
            pass

    # --- build / research via sharpy Acts -----------------------------
    async def _issue_build_or_research(self, pa: PlannedAction, now: float) -> bool:
        if self._can_use_direct_build(pa):
            return await self._get_direct_build_executor().issue_one(pa, now)

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
            elif pa.category == mapping.CAT_ADDON:
                try:
                    pa._addon_target_count = int(getattr(pa._act, "to_count"))
                except Exception:
                    pass

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

        # 记录进度快照：execute() 后若 actual_placements 增加，说明这一帧
        # 真的派出了新 SCV / 成功下单，PA 有「实质进度」——同时重置
        # running_start_time 与 wait_start_time，避免 stuck/wait abandon
        # 把正在按节奏推进的多 quantity build PA 误杀（参见 §15）。
        prev_placements = int(getattr(pa._act, "actual_placements", 0) or 0)

        try:
            done = await pa._act.execute()
        except Exception as exc:  # pragma: no cover
            logger.debug("act.execute failed for %s: %s", pa.action_name, exc)
            done = False

        cur_placements = int(getattr(pa._act, "actual_placements", 0) or 0)
        if cur_placements > prev_placements:
            pa._last_placement_progress = cur_placements
            pa.running_start_time = now
            pa.wait_start_time = now

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
                current = self._equivalent_existing_count(UnitTypeId.COMMANDCENTER)
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
                    try:
                        target = Point2.from_proto(order.target)
                    except Exception:
                        # Some build orders target a unit tag instead of a map point
                        # (for example Refinery -> geyser tag). The matching ability
                        # still means a worker is en route, but there is no position
                        # available for double-count suppression.
                        en_route += 1
                        break
                    if not self.ai.structures.closer_than(1.0, target).exists:
                        en_route += 1
                    break
        return existing + en_route

    def _build_existing_count(self, unit_type: UnitTypeId) -> int:
        if unit_type == UnitTypeId.COMMANDCENTER:
            return self._equivalent_existing_count(unit_type)
        return self._equivalent_existing_count(unit_type)

    def _build_progress_count(self, unit_type: UnitTypeId) -> int:
        if unit_type != UnitTypeId.COMMANDCENTER:
            return self._existing_plus_en_route(unit_type)

        existing = self._build_existing_count(unit_type)
        try:
            creation_ability_id = self.ai._game_data.units[unit_type.value].creation_ability.id
        except Exception:
            return existing

        en_route = 0
        for worker in self.ai.workers:
            for order in worker.orders:
                if order.ability.id != creation_ability_id:
                    continue
                try:
                    target = Point2.from_proto(order.target)
                    if not self.ai.townhalls.closer_than(2.0, target).exists:
                        en_route += 1
                except Exception:
                    en_route += 1
                break
        return existing + en_route

    def _equivalent_existing_count(self, unit_type: UnitTypeId) -> int:
        existing = self.get_count(unit_type, include_pending=False, include_not_ready=True)
        for equivalent_type in TERRAN_BUILDING_EQUIVALENTS.get(unit_type, ()):
            existing += self.cache.own(equivalent_type).amount
        flying_type = TERRAN_PRODUCTION_FLYING_EQUIVALENTS.get(unit_type)
        if flying_type is not None:
            existing += self.cache.own(flying_type).amount
        return existing

    # --- train / morph via executor selection -------------------------
    async def _issue_train_addon_morph(self, pa: PlannedAction, now: float) -> bool:
        if pa.ability is None:
            pa.state = ABANDONED
            pa.note = "abandoned: no ability id"
            return False

        if pa.category == mapping.CAT_MORPH:
            if self._cap_morph_quantity_to_possible_sources(pa):
                if pa.issued_count >= pa.quantity:
                    pa.state = DONE
                    pa.note = "done (morph already satisfied / no source)"
                    pa.wait_start_time = None
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

    def _cap_morph_quantity_to_possible_sources(self, pa: PlannedAction) -> bool:
        """Bind this PA to its own morph target and clamp impossible demand.

        Two independent ``UPGRADETOORBITAL`` PAs must not share the first
        in-progress morph as their completion condition.  The first time a PA
        is inspected, bind it to current target/in-progress count + quantity.
        """
        source_target = TERRAN_MORPH_SOURCE_TARGETS.get(pa.action_name.upper())
        if source_target is None:
            return False
        source_type, target_type = source_target
        try:
            source_total = self.cache.own(source_type).amount
            target_total = self._equivalent_existing_count(target_type)
            in_progress = self._morph_order_count(pa)
        except Exception:
            return False

        desired_total = getattr(pa, "_morph_target_total", None)
        if desired_total is None:
            desired_total = int(target_total) + int(in_progress) + int(pa.quantity)
            pa._morph_target_total = desired_total
        else:
            desired_total = int(desired_total)
        satisfied_total = int(target_total) + int(in_progress)
        if satisfied_total >= desired_total:
            pa.quantity = int(pa.issued_count)
            return True

        remaining_needed = max(0, desired_total - satisfied_total)
        possible_delta = max(0, min(int(source_total), remaining_needed))
        pa.quantity = int(pa.issued_count) + possible_delta
        return int(pa.quantity) <= int(pa.issued_count)

    def _morph_order_count(self, pa: PlannedAction) -> int:
        if pa.ability is None:
            return 0
        count = 0
        try:
            for structure in self.ai.structures:
                for order in structure.orders:
                    if order.ability.id == pa.ability:
                        count += 1
                        break
        except Exception:
            return 0
        return count

    def _choose_executor(self, pa: PlannedAction, candidates, now: float):
        units_by_tag = {u.tag: u for u, _ in candidates}

        # single candidate -> rule pick, never call the LLM
        if len(candidates) == 1:
            return candidates[0][0]

        # Only train actions may ask the Executor LLM. Morphs are deterministic:
        # prefer an idle executor, then the first candidate returned by SC2.
        if pa.category != mapping.CAT_TRAIN:
            return self._rule_choose_executor(candidates)

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
            chosen_unit = self._rule_choose_executor(candidates)

        self._executor_cache[pa.action_name] = (chosen_unit.tag, now)
        return chosen_unit

    @staticmethod
    def _rule_choose_executor(candidates):
        idle = [u for u, _ in candidates if getattr(u, "is_idle", False)]
        return idle[0] if idle else candidates[0][0]

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
