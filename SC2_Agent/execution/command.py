"""``PlannedAction`` — one command-style action tracked by the scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sc2.ids.ability_id import AbilityId

from SC2_Agent.data_tools import cost_for_action
from SC2_Agent.execution import mapping

# --- action lifecycle states ---
PENDING = "PENDING"          # not yet started this cycle
WAITING = "WAITING"          # blocked on tech-chain, producer, minerals/gas, or supply
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
    _premove_worker_tag: Optional[int] = field(default=None, repr=False)
    _premove_position: Any = field(default=None, repr=False)
    _is_gap_fill: bool = field(default=False, repr=False)

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
        if self.quantity > 1:
            return f"{self.action_name} x{self.quantity} ({self.issued_count}/{self.quantity} issued)"
        return self.action_name

    def to_dict(self) -> dict:
        return {
            "action": self.action_name,
            "category": self.category,
            "quantity": self.quantity,
            "issued": self.issued_count,
            "state": self.state,
            "cost": {
                "minerals": self.cost_minerals,
                "gas": self.cost_gas,
                "supply": self.cost_supply,
            },
            "note": self.note,
        }
