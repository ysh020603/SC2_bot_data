"""``PlannedAction`` — one command-style action tracked by the scheduler.

The lifecycle states below describe a PA's per-frame status. ``WAITING`` is
*only* held by the action stored in :attr:`ExecutionScheduler.waiter`, which
is an independent slot from the :attr:`ExecutionScheduler.actions` list.
PAs inside ``self.actions`` are guaranteed to never carry the ``WAITING``
state (the scheduler invariants enforce this).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sc2.ids.ability_id import AbilityId

from SC2_Agent.data_tools import cost_for_action
from SC2_Agent.execution import mapping

# --- action lifecycle states ---
PENDING = "PENDING"          # not yet started this cycle, lives in scheduler.actions
WAITING = "WAITING"          # blocked on tech-chain, producer, minerals/gas, or supply (held in scheduler.waiter slot)
RUNNING = "RUNNING"          # issued, in progress (build/research act running)
DONE = "DONE"                # finished / enough issued
ABANDONED = "ABANDONED"      # waited too long, given up


@dataclass
class PlannedAction:
    action_name: str
    category: str
    quantity: int = 1
    ability: Optional[AbilityId] = None
    target_result: Optional[str] = None
    cost_minerals: int = 0
    cost_gas: int = 0
    cost_supply: float = 0.0
    cost_time_frames: float = 0.0

    issued_count: int = 0
    state: str = PENDING
    enqueue_time: float = 0.0
    wait_start_time: Optional[float] = None
    # 进入 RUNNING（已下达 build/research act，但尚未成功下单）的时刻；
    # 用于侦测「act 反复返回 False、永远 RUNNING」的卡死并超时放弃。
    running_start_time: Optional[float] = None
    note: str = ""

    # --- internal runtime handles (not serialised) ---
    _act: Any = field(default=None, repr=False)
    _act_started: bool = field(default=False, repr=False)
    _act_target_count: Optional[int] = field(default=None, repr=False)
    _is_gap_fill: bool = field(default=False, repr=False)
    _direct_build_helper: Any = field(default=None, repr=False)
    _direct_build_base_count: Optional[int] = field(default=None, repr=False)
    _direct_build_target_count: Optional[int] = field(default=None, repr=False)
    _direct_build_worker_tag: Optional[int] = field(default=None, repr=False)
    _direct_build_reserved_positions: list = field(default_factory=list, repr=False)
    _direct_build_completed_positions: list = field(default_factory=list, repr=False)
    _direct_build_last_issue_time: Optional[float] = field(default=None, repr=False)
    _direct_build_attempts: int = field(default=0, repr=False)
    _defer_until_build_type: Any = field(default=None, repr=False)
    _defer_reason: str = field(default="", repr=False)
    _defer_created_time: Optional[float] = field(default=None, repr=False)
    # 用于侦测 build act 的进度推进：每次新派出 SCV/下单成功（`actual_placements`
    # 增加），scheduler 会更新此快照并把 `running_start_time` 重置为当前时刻。
    # 这样 `_abandon_stuck_running` 不会在「多 quantity build 正在按节奏推进」时
    # 误杀 PA（参见 docs/同类建筑重复下单与提前完成问题分析.md §15）。
    _last_placement_progress: int = field(default=0, repr=False)

    @classmethod
    def from_action_name(
        cls,
        action_name: str,
        quantity: int = 1,
        *,
        is_gap_fill: bool = False,
    ) -> "PlannedAction":
        info = cost_for_action(action_name)
        cost = info.get("cost") or {}
        category = mapping.category_for(action_name)
        return cls(
            action_name=action_name,
            category=category,
            quantity=max(1, int(quantity)),
            ability=mapping.ability_for(action_name),
            target_result=info.get("target_result"),
            cost_minerals=int(cost.get("minerals", 0) or 0),
            cost_gas=int(cost.get("gas", 0) or 0),
            cost_supply=float(cost.get("supply", 0) or 0),
            cost_time_frames=float(cost.get("time", 0) or 0),
            _is_gap_fill=is_gap_fill,
        )

    # --- helpers ---
    def is_terminal(self) -> bool:
        return self.state in (DONE, ABANDONED)

    def is_waiting(self) -> bool:
        return self.state == WAITING

    def short_label(self) -> str:
        suffix = " [deferred]" if self._defer_until_build_type is not None else ""
        if self.quantity > 1:
            return f"{self.action_name} x{self.quantity} ({self.issued_count}/{self.quantity} issued){suffix}"
        return f"{self.action_name}{suffix}"

    def to_dict(self) -> dict:
        return {
            "action": self.action_name,
            "category": self.category,
            "quantity": self.quantity,
            "issued": self.issued_count,
            "state": self.state,
            "priority": self.priority_tier(),
            "cost": {
                "minerals": self.cost_minerals,
                "gas": self.cost_gas,
                "supply": self.cost_supply,
            },
            "note": self.note,
            "deferred": self._defer_until_build_type is not None,
            "defer_reason": self._defer_reason,
        }

    def priority_tier(self) -> int:
        """Return the priority tier (lower = higher) used by the scheduler.

        Mirrors :py:func:`SC2_Agent.execution.scheduler._priority_for` but
        kept inline here to avoid an import cycle. ``cost.supply``:

        * ``< 0`` -> tier 0 (supply provider, e.g. SupplyDepot, CommandCenter)
        * ``== 0`` -> tier 1 (supply neutral)
        * ``> 0`` -> tier 2 (supply consumer, train/morph)
        """
        try:
            s = float(self.cost_supply or 0)
        except (TypeError, ValueError):
            s = 0.0
        if s < 0:
            return 0
        if s > 0:
            return 2
        return 1
