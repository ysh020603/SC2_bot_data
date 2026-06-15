"""Command-style execution scheduler (replaces the declarative ActLLMOngoingTasks).

``ExecutionScheduler`` is a sharpy ``ActBase`` driven every frame. It walks an
ordered list of :class:`PlannedAction` and enforces:

* **prerequisite / tech-chain checks** via ``data_tools.prereq_runtime`` (obs
  three-state aware): if a prerequisite is already building it WAITS; otherwise
  it inserts the missing prerequisite action(s) before the blocked one.
* **resource reservation**: an action that cannot afford minerals/gas reserves
  its full cost; later actions only proceed against the surplus, so an action
  that is not blocked by the contended resource can overtake.
* **20s abandonment**: an action waiting too long is dropped and its reservation
  released.
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
    gap_fill_actions,
    is_available_now,
)
from SC2_Agent.execution import mapping
from SC2_Agent.execution.command import (
    ABANDONED,
    DONE,
    PENDING,
    RUNNING,
    WAITING_RES,
    WAITING_TECH,
    PlannedAction,
)
from SC2_Agent.execution import executor_select

logger = logging.getLogger("SC2_Agent.execution.scheduler")

#: 22.4 game frames per second on "Faster" speed.
FRAMES_PER_SECOND = 22.4
#: Executor-LLM result cache lifetime (seconds) to throttle execution-time calls.
EXECUTOR_CACHE_SEC = 3.0


#: Type of the executor-LLM callback the bot injects.
#: (ability_name, candidate_text, cost_hint, pending_summary,
#:  waiting_summary, conflict_hints, legal_tags) -> Optional[int]
ExecutorLLM = Callable[..., Optional[int]]


class ExecutionScheduler(ActBase):
    def __init__(self, wait_abandon_sec: float = 20.0):
        super().__init__()
        self.actions: List[PlannedAction] = []
        self.wait_abandon_sec = wait_abandon_sec
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
            self.actions = kept + new_actions
        else:
            self.actions = new_actions
        self._executor_cache.clear()
        logger.info("Scheduler installed %d actions (mode=%s)", len(new_actions), mode)

    def is_drained(self) -> bool:
        return all(a.is_terminal() for a in self.actions) if self.actions else True

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
    # main per-frame loop
    # ------------------------------------------------------------------
    async def execute(self) -> bool:
        if not self.actions:
            return True  # non-blocking: let background tactics run

        now = self.ai.time
        reserved_min = 0.0
        reserved_gas = 0.0
        spent_min = 0.0
        spent_gas = 0.0

        index = 0
        while index < len(self.actions):
            pa = self.actions[index]
            index += 1

            if pa.is_terminal():
                continue

            # 1) abandonment timeout
            if pa.wait_start_time is not None and (now - pa.wait_start_time) > self.wait_abandon_sec:
                pa.state = ABANDONED
                pa.note = "abandoned: waited too long"
                continue

            # 2) prerequisite / tech-chain gate
            try:
                available = is_available_now(self.ai, pa.action_name)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("prereq check failed for %s: %s", pa.action_name, exc)
                available = True

            if not available:
                if self._chain_in_progress_safe(pa):
                    self._enter_wait(pa, WAITING_TECH, now, "waiting: prerequisite in progress")
                else:
                    inserted = self._insert_gap_fills(pa, index - 1)
                    self._enter_wait(
                        pa,
                        WAITING_TECH,
                        now,
                        "waiting: inserted prerequisites" if inserted else "waiting: tech missing",
                    )
                    if inserted:
                        # New prerequisites were spliced in before this action;
                        # process them on the next frames.
                        break
                continue

            # 3) resource gate (reservation-aware). Use the live ability cost so
            #    morph/add-on deltas are accurate (the DB stores cumulative cost
            #    for morphed units e.g. OrbitalCommand=550).
            need_min, need_gas = self._live_cost(pa)
            avail_min = self.ai.minerals - reserved_min - spent_min
            avail_gas = self.ai.vespene - reserved_gas - spent_gas

            if avail_min >= need_min and avail_gas >= need_gas:
                issued = await self._try_issue(pa, now)
                if issued:
                    spent_min += need_min
                    spent_gas += need_gas
                # if not issued (e.g. producer busy) _try_issue set the wait state
            else:
                reserved_min += need_min
                reserved_gas += need_gas
                self._enter_wait(pa, WAITING_RES, now, "waiting: resources")
                if pa.category == mapping.CAT_BUILD:
                    await self._premove_scv(pa)

        return True

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _enter_wait(self, pa: PlannedAction, state: str, now: float, note: str) -> None:
        if pa.wait_start_time is None or not pa.is_waiting():
            pa.wait_start_time = now
        pa.state = state
        pa.note = note

    def _chain_in_progress_safe(self, pa: PlannedAction) -> bool:
        try:
            return chain_in_progress(self.ai, pa.action_name)
        except Exception:
            return False

    def _insert_gap_fills(self, pa: PlannedAction, index: int) -> bool:
        try:
            fills = gap_fill_actions(self.ai, pa.action_name)
        except Exception as exc:  # pragma: no cover
            logger.debug("gap_fill failed for %s: %s", pa.action_name, exc)
            return False
        if not fills:
            return False
        existing = {a.action_name for a in self.actions if not a.is_terminal()}
        new_pas = [
            PlannedAction.from_action_name(f, 1, is_gap_fill=True)
            for f in fills
            if f not in existing
        ]
        if not new_pas:
            return False
        now = self.ai.time
        for npa in new_pas:
            npa.enqueue_time = now
        self.actions[index:index] = new_pas
        logger.info(
            "Inserted gap-fill prerequisites %s before %s",
            [p.action_name for p in new_pas],
            pa.action_name,
        )
        return True

    async def _try_issue(self, pa: PlannedAction, now: float) -> bool:
        """Issue one step of ``pa``. Returns True if resources were committed."""
        if pa.category in (mapping.CAT_BUILD, mapping.CAT_RESEARCH):
            return await self._issue_build_or_research(pa)
        return await self._issue_train_addon_morph(pa, now)

    # --- build / research via sharpy Acts -----------------------------
    async def _issue_build_or_research(self, pa: PlannedAction) -> bool:
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

        try:
            done = await pa._act.execute()
        except Exception as exc:  # pragma: no cover
            logger.debug("act.execute failed for %s: %s", pa.action_name, exc)
            done = False

        if done:
            pa.state = DONE
            pa.issued_count = pa.quantity
            pa.note = "done"
        else:
            pa.state = RUNNING
            pa.wait_start_time = None
            pa.note = "building/researching"
        return True

    def _create_sharpy_act(self, pa: PlannedAction):
        if pa.category == mapping.CAT_RESEARCH:
            return mapping.make_research_act(pa.target_result)
        to_count = self._compute_build_to_count(pa)
        return mapping.make_build_act(pa.action_name, pa.target_result, to_count)

    def _compute_build_to_count(self, pa: PlannedAction) -> int:
        upper = pa.action_name.upper()
        try:
            if upper == "TERRANBUILD_COMMANDCENTER" or pa.target_result == "CommandCenter":
                current = self.ai.townhalls.amount
            elif "REFINERY" in upper:
                current = self.get_count(UnitTypeId.REFINERY)
            else:
                unit_type = mapping.unit_type_for(pa.target_result or "")
                current = self.get_count(unit_type) if unit_type else 0
        except Exception:
            current = 0
        return int(current) + int(pa.quantity)

    # --- train / addon / morph via executor selection -----------------
    async def _issue_train_addon_morph(self, pa: PlannedAction, now: float) -> bool:
        if pa.ability is None:
            pa.state = ABANDONED
            pa.note = "abandoned: no ability id"
            return False

        candidates = await executor_select.candidate_executors(self.ai, pa.ability)
        if not candidates:
            self._enter_wait(pa, WAITING_TECH, now, "waiting: no free producer")
            return False

        chosen = self._choose_executor(pa, candidates, now)
        if chosen is None:
            self._enter_wait(pa, WAITING_TECH, now, "waiting: no executor chosen")
            return False

        try:
            chosen(pa.ability)
        except Exception as exc:  # pragma: no cover
            logger.debug("issue failed for %s on tag %s: %s", pa.action_name, chosen.tag, exc)
            self._enter_wait(pa, WAITING_TECH, now, "waiting: issue failed")
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
        tags = executor_select.candidate_tags(candidates)
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
                tag = self.executor_llm(
                    ability_name=pa.action_name,
                    candidate_text=executor_select.candidates_text(candidates),
                    cost_hint=self._cost_hint(pa),
                    pending_summary=self.pending_summary_text(),
                    waiting_summary=self._waiting_summary(exclude=pa),
                    conflict_hints=executor_select.executor_conflict_hints(candidates, pending_names),
                    legal_tags=tags,
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
