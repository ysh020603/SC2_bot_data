"""Direct building executor for command-style scheduler actions.

This module keeps Sharpy's useful placement and worker-selection logic, but it
does not delegate lifecycle ownership to ``GridBuilding.execute``. The scheduler
remains responsible for quantity, retry, and DONE semantics.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Tuple

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from sharpy.plans.acts.grid_building import GridBuilding, INVALID_POSITION_THRESHOLD

from SC2_Agent.execution import mapping
from SC2_Agent.execution.command import DONE, RUNNING, PlannedAction

logger = logging.getLogger("SC2_Agent.execution.direct_build")

DIRECT_BUILD_CONFIRM_TIMEOUT = 45.0

DIRECT_TERRAN_BUILD_TYPES = {
    UnitTypeId.SUPPLYDEPOT,
    UnitTypeId.BARRACKS,
    UnitTypeId.ENGINEERINGBAY,
    UnitTypeId.FACTORY,
    UnitTypeId.ARMORY,
    UnitTypeId.MISSILETURRET,
    UnitTypeId.BUNKER,
    UnitTypeId.SENSORTOWER,
    UnitTypeId.GHOSTACADEMY,
    UnitTypeId.STARPORT,
    UnitTypeId.FUSIONCORE,
}


def direct_build_unit_type(pa: PlannedAction) -> Optional[UnitTypeId]:
    if pa.category != mapping.CAT_BUILD:
        return None
    unit_type = mapping.unit_type_for(pa.target_result or "")
    if unit_type in DIRECT_TERRAN_BUILD_TYPES:
        return unit_type
    return None


class DirectBuildExecutor:
    """Issue one concrete worker build command at a time for a PA."""

    def __init__(self, scheduler):
        self.scheduler = scheduler

    @property
    def ai(self):
        return self.scheduler.ai

    async def mark_done_if_satisfied(self, pa: PlannedAction, now: float) -> bool:
        unit_type = direct_build_unit_type(pa)
        if unit_type is None:
            return False
        await self._ensure_helper(pa, unit_type)
        self._ensure_target(pa, unit_type)
        self._purge_reservations(pa, unit_type, now)
        existing, en_route, fresh = self._owned_progress_counts(pa, unit_type)
        self._refresh_issued_count(pa, existing, en_route, include_fresh=True, fresh=fresh)
        if existing + en_route >= int(pa._direct_build_target_count or 0):
            self._finish(pa, unit_type, existing, en_route)
            return True
        if existing + en_route + fresh >= int(pa._direct_build_target_count or 0):
            self._mark_running(pa, now, "building: waiting for engine confirmation")
            return True
        return False

    async def issue_one(self, pa: PlannedAction, now: float) -> bool:
        unit_type = direct_build_unit_type(pa)
        if unit_type is None:
            return False

        helper = await self._ensure_helper(pa, unit_type)
        self._ensure_target(pa, unit_type)
        self._purge_reservations(pa, unit_type, now)

        existing, en_route, fresh = self._owned_progress_counts(pa, unit_type)
        self._refresh_issued_count(pa, existing, en_route, include_fresh=True)

        target = int(pa._direct_build_target_count or 0)
        if existing + en_route >= target:
            self._finish(pa, unit_type, existing, en_route)
            return False

        if existing + en_route + fresh >= target:
            pa.state = RUNNING
            pa.wait_start_time = None
            if pa.running_start_time is None:
                pa.running_start_time = now
            pa.note = "building: waiting for engine confirmation"
            return False

        global_existing, global_en_route = self._progress_counts(unit_type)
        position = await self._select_position(helper, unit_type, global_existing + global_en_route)
        if position is None:
            self._mark_running(pa, now, "building: no valid placement")
            return False

        worker = helper.get_worker_builder(position, pa._direct_build_worker_tag, helper.only_roles)
        if worker is None:
            pa._direct_build_worker_tag = None
            self._mark_running(pa, now, "building: no worker")
            return False

        if helper.has_build_order(worker) or worker.tag in self.ai.unit_tags_received_action:
            helper.set_worker(worker)
            pa._direct_build_worker_tag = worker.tag
            self._mark_running(pa, now, "building: worker busy")
            return False

        helper.set_worker(worker)
        worker.build(unit_type, position)

        pa._direct_build_worker_tag = worker.tag
        pa._direct_build_reserved_positions.append((position, now, worker.tag))
        pa._direct_build_last_issue_time = now
        pa._direct_build_attempts += 1
        pa.running_start_time = now
        pa.wait_start_time = None
        pa.state = RUNNING
        pa.note = "building: direct command issued"

        fresh_after = len(pa._direct_build_reserved_positions)
        self._refresh_issued_count(pa, existing, en_route, include_fresh=True, fresh=fresh_after)
        self._emit(
            pa,
            "[DirectBuild] %s target=%d progress=%d existing=%d en_route=%d fresh=%d "
            "pos=(%.1f, %.1f) worker=%s attempt=%d",
            unit_type.name,
            target,
            existing + en_route + fresh_after,
            existing,
            en_route,
            fresh_after,
            position.x,
            position.y,
            worker.tag,
            pa._direct_build_attempts,
        )
        return True

    def clear_worker(self, pa: PlannedAction) -> None:
        helper = getattr(pa, "_direct_build_helper", None)
        if helper is not None and getattr(helper, "clear_worker", None):
            try:
                helper.clear_worker()
            except Exception:
                pass
        pa._direct_build_worker_tag = None

    def keep_waiting_if_progressing(self, pa: PlannedAction, now: float) -> bool:
        """Do not abandon a partial multi-building order that is waiting on resources."""
        unit_type = direct_build_unit_type(pa)
        if unit_type is None or pa._direct_build_target_count is None:
            return False

        self._purge_reservations(pa, unit_type, now)
        existing, en_route, fresh = self._owned_progress_counts(pa, unit_type)
        target = int(pa._direct_build_target_count or 0)
        progress = existing + en_route + fresh
        if progress <= 0 or existing + en_route >= target:
            return False

        pa.wait_start_time = now
        pa.note = "waiting: resources (direct build progress)"
        self._emit(
            pa,
            "[DirectBuild] %s keep waiting target=%d progress=%d existing=%d en_route=%d fresh=%d",
            unit_type.name,
            target,
            progress,
            existing,
            en_route,
            fresh,
        )
        return True

    async def _ensure_helper(self, pa: PlannedAction, unit_type: UnitTypeId) -> GridBuilding:
        helper = getattr(pa, "_direct_build_helper", None)
        if helper is None or getattr(helper, "unit_type", None) != unit_type:
            helper = GridBuilding(unit_type, 0)
            await helper.start(self.scheduler.knowledge)
            pa._direct_build_helper = helper
        helper.builder_tag = pa._direct_build_worker_tag
        helper.to_count = int(pa._direct_build_target_count or 0)
        return helper

    def _ensure_target(self, pa: PlannedAction, unit_type: UnitTypeId) -> None:
        if pa._direct_build_target_count is not None:
            return
        pa._direct_build_base_count = 0
        pa._direct_build_target_count = int(pa.quantity)

    def _progress_counts(self, unit_type: UnitTypeId) -> Tuple[int, int]:
        existing = self.scheduler._equivalent_existing_count(unit_type)
        return existing, len(self._worker_order_positions(unit_type))

    def _worker_order_positions(self, unit_type: UnitTypeId) -> List[Point2]:
        try:
            creation_ability_id = self.ai._game_data.units[unit_type.value].creation_ability.id
        except Exception:
            return []
        positions: List[Point2] = []
        for worker in self.ai.workers:
            for order in worker.orders:
                if order.ability.id != creation_ability_id:
                    continue
                try:
                    position = Point2.from_proto(order.target)
                except Exception:
                    break
                if not self.ai.structures.closer_than(1.0, position).exists:
                    positions.append(position)
                break
        return self._dedupe_positions(positions)

    def _purge_reservations(self, pa: PlannedAction, unit_type: UnitTypeId, now: float) -> None:
        helper = getattr(pa, "_direct_build_helper", None)
        order_positions = self._worker_order_positions(unit_type)
        kept = []
        for position, issue_time, worker_tag in list(pa._direct_build_reserved_positions):
            if self.ai.structures.closer_than(1.0, position).exists:
                self._remember_completed_position(pa, position)
                continue
            if self._has_near(order_positions, position, 1.0):
                kept.append((position, issue_time, worker_tag))
                continue
            if now - float(issue_time) > DIRECT_BUILD_CONFIRM_TIMEOUT:
                if helper is not None:
                    helper.invalid_positions[position] = INVALID_POSITION_THRESHOLD
                self._emit(
                    pa,
                    "[DirectBuild] %s retry position=(%.1f, %.1f) worker=%s reason=no_confirmation_after_%.0fs",
                    unit_type.name,
                    position.x,
                    position.y,
                    worker_tag,
                    DIRECT_BUILD_CONFIRM_TIMEOUT,
                )
                continue
            kept.append((position, issue_time, worker_tag))
        pa._direct_build_reserved_positions = kept

    def _owned_progress_counts(self, pa: PlannedAction, unit_type: UnitTypeId) -> Tuple[int, int, int]:
        order_positions = self._worker_order_positions(unit_type)
        existing = self._count_still_existing(pa._direct_build_completed_positions)
        en_route = 0
        fresh = 0
        for position, _issue_time, _worker_tag in pa._direct_build_reserved_positions:
            if self.ai.structures.closer_than(1.0, position).exists:
                existing += 1
            elif self._has_near(order_positions, position, 1.0):
                en_route += 1
            else:
                fresh += 1
        return existing, en_route, fresh

    def _count_still_existing(self, positions: Iterable[Point2]) -> int:
        count = 0
        for position in self._dedupe_positions(positions):
            if self.ai.structures.closer_than(1.0, position).exists:
                count += 1
        return count

    def _remember_completed_position(self, pa: PlannedAction, position: Point2) -> None:
        if not self._has_near(pa._direct_build_completed_positions, position, 1.0):
            pa._direct_build_completed_positions.append(position)

    async def _select_position(self, helper: GridBuilding, unit_type: UnitTypeId, count: int) -> Optional[Point2]:
        position = await helper.position_terran(count)
        if position is None:
            return None
        if self._is_fresh_reserved(helper, position):
            return None
        if self._has_near(self._worker_order_positions(unit_type), position, 1.0):
            return None
        return position

    def _is_fresh_reserved(self, helper: GridBuilding, position: Point2) -> bool:
        # Scan both the action list and the (independent) waiter slot so that a
        # newly promoted waiter's pending reservation is honoured during this
        # frame's scan of unrelated PAs.
        for pa in self.scheduler.all_planned_actions():
            for reserved, _issue_time, _worker_tag in getattr(pa, "_direct_build_reserved_positions", []):
                if reserved.distance_to_point2(position) < 1.0:
                    return True
        return False

    def _refresh_issued_count(
        self,
        pa: PlannedAction,
        existing: int,
        en_route: int,
        *,
        include_fresh: bool,
        fresh: Optional[int] = None,
    ) -> None:
        if fresh is None:
            fresh = len(pa._direct_build_reserved_positions) if include_fresh else 0
        base = int(pa._direct_build_base_count or 0)
        pa.issued_count = max(0, min(int(pa.quantity), int(existing + en_route + fresh - base)))

    def _finish(self, pa: PlannedAction, unit_type: UnitTypeId, existing: int, en_route: int) -> None:
        pa.state = DONE
        pa.issued_count = int(pa.quantity)
        pa.note = "done (direct build in flight)"
        pa.running_start_time = None
        pa.wait_start_time = None
        pa._direct_build_reserved_positions = []
        pa._direct_build_completed_positions = []
        self.clear_worker(pa)
        self._emit(
            pa,
            "[DirectBuild] %s DONE target=%d existing=%d en_route=%d",
            unit_type.name,
            int(pa._direct_build_target_count or 0),
            existing,
            en_route,
        )

    def _mark_running(self, pa: PlannedAction, now: float, note: str) -> None:
        pa.state = RUNNING
        pa.wait_start_time = None
        if pa.running_start_time is None:
            pa.running_start_time = now
        pa.note = note

    @staticmethod
    def _dedupe_positions(positions: Iterable[Point2]) -> List[Point2]:
        unique: List[Point2] = []
        for position in positions:
            if not DirectBuildExecutor._has_near(unique, position, 1.0):
                unique.append(position)
        return unique

    @staticmethod
    def _has_near(positions: Iterable[Point2], target: Point2, radius: float) -> bool:
        for position in positions:
            if position.distance_to_point2(target) < radius:
                return True
        return False

    @staticmethod
    def _emit(pa: PlannedAction, message: str, *args) -> None:
        logger.info(message, *args)
        helper = getattr(pa, "_direct_build_helper", None)
        if helper is not None and getattr(helper, "print", None):
            try:
                helper.print(message % args)
            except Exception:
                pass
