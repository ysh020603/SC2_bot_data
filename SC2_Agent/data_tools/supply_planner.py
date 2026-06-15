"""Insert supply-depot actions into an ordered action list to avoid supply block.

Given the ordered (supply-depot-free) action list produced by the ordering LLM
and the live bot state, this simulates the running supply headroom and inserts
``TERRANBUILD_SUPPLYDEPOT`` actions wherever the projected free supply would drop
below a threshold (default 8). If the current supply is already very redundant,
few or no depots are inserted (the projection simply never crosses the
threshold), avoiding wasted minerals.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

try:  # package import (normal runtime)
    from .action_cost import cost_for_action
    from .sc2_data_common import canonical_ability_name, load_database
except ImportError:  # pragma: no cover
    from action_cost import cost_for_action  # type: ignore
    from sc2_data_common import canonical_ability_name, load_database  # type: ignore


SUPPLY_DEPOT_ACTION = "TERRANBUILD_SUPPLYDEPOT"
SUPPLY_DEPOT_PROVIDES = 8.0
MAX_SUPPLY_CAP = 200
#: Safety cap so a degenerate plan cannot insert an unbounded number of depots.
MAX_INSERTED_DEPOTS = 8


@lru_cache(maxsize=1)
def _data():
    return load_database()


@lru_cache(maxsize=256)
def _supply_delta(action_name: str) -> float:
    """Supply delta of an action (positive consumes, negative provides cap)."""
    info = cost_for_action(action_name)
    cost = info.get("cost") or {}
    try:
        return float(cost.get("supply", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _current_free_supply(ai: Any) -> float:
    """Free supply now, plus headroom already coming from pending depots."""
    try:
        free = float(getattr(ai, "supply_left", 0) or 0)
    except (TypeError, ValueError):
        free = 0.0
    # Count depots under construction (they will add cap soon).
    pending_depot_cap = 0.0
    try:
        from sc2.ids.unit_typeid import UnitTypeId  # local import; optional

        already_pending = getattr(ai, "already_pending", None)
        if callable(already_pending):
            pending_depot_cap = SUPPLY_DEPOT_PROVIDES * float(
                already_pending(UnitTypeId.SUPPLYDEPOT)
            )
    except Exception:
        pending_depot_cap = 0.0
    return free + pending_depot_cap


def plan(
    ordered_actions: list[str],
    ai: Any,
    *,
    threshold: float = 8.0,
) -> list[str]:
    """Return a new ordered action list with supply depots inserted as needed.

    :param ordered_actions: canonical action names (no supply depots expected).
    :param ai:              live bot, used for current free supply.
    :param threshold:       minimum projected free supply to maintain.
    """
    canon = [canonical_ability_name(_data(), a) for a in ordered_actions]
    projected_free = _current_free_supply(ai)
    out: list[str] = []
    inserted = 0

    for action in canon:
        delta = _supply_delta(action)
        consumes = max(delta, 0.0)

        # Insert depots until executing this action keeps headroom >= threshold.
        while (
            consumes > 0
            and (projected_free - consumes) < threshold
            and inserted < MAX_INSERTED_DEPOTS
        ):
            out.append(SUPPLY_DEPOT_ACTION)
            projected_free += SUPPLY_DEPOT_PROVIDES
            inserted += 1

        out.append(action)
        # Apply this action's effect on supply headroom.
        projected_free -= delta  # delta<0 (depot/CC) raises headroom
        if projected_free > MAX_SUPPLY_CAP:
            projected_free = MAX_SUPPLY_CAP

    return out
