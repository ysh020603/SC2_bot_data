"""Return mineral, gas, and supply cost for an action via its result entity.

Vendored from DATA_TOOLS/tools/action_cost.py (import path adapted for the
``SC2_Agent.data_tools`` package).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import (
        action_result_names,
        build_ability_index,
        build_entity_indexes,
        canonical_ability_name,
        load_database,
        target_kind_and_result,
    )
except ImportError:  # pragma: no cover - allow running as a loose script
    from sc2_data_common import (  # type: ignore
        action_result_names,
        build_ability_index,
        build_entity_indexes,
        canonical_ability_name,
        load_database,
        target_kind_and_result,
    )


def _unit_cost(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "minerals": unit.get("minerals", 0),
        "gas": unit.get("gas", 0),
        "supply": unit.get("supply", 0),
        "time": unit.get("time", 0),
    }


def _upgrade_cost(upgrade: dict[str, Any]) -> dict[str, Any]:
    cost = upgrade.get("cost") or {}
    return {
        "minerals": cost.get("minerals", 0),
        "gas": cost.get("gas", 0),
        "supply": cost.get("supply", 0),
        "time": cost.get("time", 0),
    }


def cost_for_action(action_name: str, *, data_path: str | Path | None = None) -> dict[str, Any]:
    data = load_database(data_path)
    ability_index = build_ability_index(data)
    units, upgrades = build_entity_indexes(data)

    canonical_action = canonical_ability_name(data, action_name)
    ability = ability_index.get(canonical_action)
    if ability is None:
        return {
            "action_name": action_name,
            "known": False,
            "results": [],
            "cost": None,
        }

    target_kind, target_result = target_kind_and_result(ability)
    result_names = action_result_names(ability)
    if target_result and target_result not in result_names:
        result_names.append(target_result)

    results = []
    for result_name in result_names:
        if result_name in units:
            entity_type = "Unit"
            cost = _unit_cost(units[result_name])
        elif result_name in upgrades:
            entity_type = "Upgrade"
            cost = _upgrade_cost(upgrades[result_name])
        else:
            entity_type = None
            cost = None

        results.append(
            {
                "entity_name": result_name,
                "entity_type": entity_type,
                "cost": cost,
            }
        )

    primary_cost = results[0]["cost"] if results else None
    return {
        "action_name": action_name,
        "ability_name": canonical_action,
        "known": True,
        "target_kind": target_kind,
        "target_result": target_result,
        "results": results,
        "cost": primary_cost,
        "note": "Cost is read from the result Unit or Upgrade entry in the database.",
    }
