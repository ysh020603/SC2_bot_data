"""Insert supply-depot actions into an ordered action list to avoid supply block.

Given the ordered (supply-depot-free) action list produced by the ordering LLM
and the live bot state, this simulates the running supply headroom and inserts
``TERRANBUILD_SUPPLYDEPOT`` actions before upcoming training bursts. The planner
keeps the historical low-water threshold, but it now sizes depot insertion from
the near-future training demand plus a small reserve instead of checking every
single unit in isolation.
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
DEFAULT_TRAINING_RESERVE = 4.0


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


@lru_cache(maxsize=256)
def _target_kind(action_name: str) -> str | None:
    info = cost_for_action(action_name)
    kind = info.get("target_kind")
    return str(kind) if kind else None


def _is_supply_provider_counted_now(action_name: str) -> bool:
    """Only count near-term supply providers that are meant as supply actions.

    Command Centers also provide supply in the data table, but treating that cap
    as immediately available makes Stage5 skip depots and causes short-term
    supply blocks while the expansion is still building.
    """
    return action_name == SUPPLY_DEPOT_ACTION


def _training_burst_demand(actions: list[str], start: int) -> float:
    """Supply needed by the consecutive Train actions starting at ``start``."""
    demand = 0.0
    for action in actions[start:]:
        if _target_kind(action) != "Train":
            break
        demand += max(_supply_delta(action), 0.0)
    return demand


def current_free_supply(ai: Any) -> float:
    """Free supply now, plus headroom already coming from pending depots."""
    return _current_free_supply(ai)


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


def plan_with_trace(
    ordered_actions: list[str],
    ai: Any,
    *,
    threshold: float = 8.0,
    training_reserve: float = DEFAULT_TRAINING_RESERVE,
) -> tuple[list[str], list[str]]:
    """Same as :func:`plan` but also returns a human-readable derivation trace.

    The trace explains, step by step, the projected free-supply headroom before
    each action, why (and when) a supply depot is injected, and the headroom
    after applying each action's supply effect. Useful for logging / debugging
    how the insertion plan was derived.

    :returns: ``(planned_actions, trace_lines)``
    """
    canon = [canonical_ability_name(_data(), a) for a in ordered_actions]
    projected_free = _current_free_supply(ai)
    out: list[str] = []
    inserted = 0

    trace: list[str] = [
        f"start: free_supply~={projected_free:.0f} "
        f"(supply_left={getattr(ai, 'supply_left', '?')} + pending depots), "
        f"threshold={threshold:.0f}, training_reserve={training_reserve:.0f}"
    ]

    for index, action in enumerate(canon):
        delta = _supply_delta(action)
        consumes = max(delta, 0.0)
        is_train = _target_kind(action) == "Train"
        is_train_burst_start = is_train and (
            index == 0 or _target_kind(canon[index - 1]) != "Train"
        )
        burst_demand = _training_burst_demand(canon, index) if is_train_burst_start else consumes
        if is_train_burst_start:
            required_free = max(threshold, burst_demand + training_reserve)
            reserve_used = training_reserve
            threshold_used = threshold
        elif is_train:
            required_free = consumes
            reserve_used = 0.0
            threshold_used = 0.0
        else:
            required_free = max(threshold, consumes + training_reserve)
            reserve_used = training_reserve
            threshold_used = threshold

        # Insert depots before a supply-consuming demand point. For Train actions,
        # size this from the whole consecutive training burst plus reserve.
        while (
            consumes > 0
            and projected_free < required_free
            and inserted < MAX_INSERTED_DEPOTS
        ):
            before = projected_free
            out.append(SUPPLY_DEPOT_ACTION)
            projected_free += SUPPLY_DEPOT_PROVIDES
            inserted += 1
            trace.append(
                f"  insert {SUPPLY_DEPOT_ACTION}: before {action} need "
                f"{required_free:.0f} free ({'training_burst' if is_train_burst_start else 'action'} "
                f"demand={burst_demand:.0f} + reserve={reserve_used:.0f}, "
                f"threshold={threshold_used:.0f}), had {before:.0f} "
                f"-> +{SUPPLY_DEPOT_PROVIDES:.0f} (free {before:.0f}->{projected_free:.0f})"
            )

        out.append(action)
        before = projected_free
        # Apply this action's effect on near-term supply headroom. Count explicit
        # depot cap, but do not spend Command Center cap before it actually
        # finishes.
        if delta < 0 and not _is_supply_provider_counted_now(action):
            trace.append(
                f"  {action}: delayed supply provider {delta:+.0f}; "
                f"not counted for near-term headroom (free {projected_free:.0f})"
            )
            continue
        projected_free -= delta  # delta<0 for counted supply providers raises headroom
        if projected_free > MAX_SUPPLY_CAP:
            projected_free = MAX_SUPPLY_CAP
        if delta:
            trace.append(
                f"  {action}: supply_delta={delta:+.0f} (free {before:.0f}->{projected_free:.0f})"
            )
        else:
            trace.append(f"  {action}: no supply cost (free {projected_free:.0f})")

    if inserted == 0:
        trace.append("no depot inserted (headroom never dropped below threshold)")
    else:
        trace.append(f"inserted {inserted} supply depot(s)")
    return out, trace


def plan(
    ordered_actions: list[str],
    ai: Any,
    *,
    threshold: float = 8.0,
    training_reserve: float = DEFAULT_TRAINING_RESERVE,
) -> list[str]:
    """Return a new ordered action list with supply depots inserted as needed.

    :param ordered_actions: canonical action names (no supply depots expected).
    :param ai:              live bot, used for current free supply.
    :param threshold:       minimum projected free supply to maintain.
    """
    out, _trace = plan_with_trace(
        ordered_actions, ai, threshold=threshold, training_reserve=training_reserve
    )
    return out
