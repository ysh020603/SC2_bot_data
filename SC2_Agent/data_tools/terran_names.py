"""Canonical Terran Unit/Upgrade name lists.

The naming LLM (stage 2) maps free-form natural-language increments onto the
canonical entity names used by ``data_base_add_graph.json``. This module
exports:

* ``terran_unit_names()``: macro-selectable Terran Unit names.
* ``terran_upgrade_names()``: canonical Terran Upgrade names filtered by
  Terran action prefixes in their ``tech_chain``.
* ``is_known_terran_entity()``: exact canonical-name validation.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import (
        action_result_names,
        build_ability_index,
        build_entity_indexes,
        build_executor_index,
        load_database,
        target_kind_and_result,
    )
except ImportError:  # pragma: no cover
    from sc2_data_common import (  # type: ignore
        action_result_names,
        build_ability_index,
        build_entity_indexes,
        build_executor_index,
        load_database,
        target_kind_and_result,
    )


# Action-name fragments that mark an Upgrade as Terran-researchable.
_TERRAN_ACTION_PREFIXES = (
    "TERRANBUILD_",
    "COMMANDCENTER",
    "BARRACKS",
    "FACTORY",
    "STARPORT",
    "ENGINEERINGBAY",
    "ARMORY",
    "FUSIONCORE",
    "GHOSTACADEMY",
    "BUILD_TECHLAB",
    "BUILD_REACTOR",
    "LIFT_",
    "LAND_",
    "MORPH_",
    "HISECAUTOTRACKING",
)

_MACRO_UNIT_TARGET_KINDS = {
    "Build",
    "BuildOnUnit",
    "BuildInstant",
    "Train",
}

# Macro-significant morph targets are not "built" or "trained", but they are
# valid economy/tech goals for Stage2. Pure unit mode states stay out of the
# naming table and are handled by tactics or explicit post-processing instead.
_MACRO_MORPH_UNIT_NAMES = {
    "OrbitalCommand",
    "PlanetaryFortress",
}


def _is_terran_upgrade(upgrade: dict[str, Any]) -> bool:
    chains = " ".join(upgrade.get("tech_chain") or [])
    if not chains:
        return False
    return any(prefix in chains for prefix in _TERRAN_ACTION_PREFIXES)


def _is_macro_selectable_unit(
    name: str,
    ability_index: dict[str, dict[str, Any]],
    executor_index: dict[str, set[str]],
) -> bool:
    for ability_name, ability in ability_index.items():
        if not executor_index.get(ability_name):
            continue
        if name not in action_result_names(ability):
            continue
        target_kind, _target_result = target_kind_and_result(ability)
        if target_kind not in _MACRO_UNIT_TARGET_KINDS | {"Morph", "MorphPlace"}:
            continue
        executors = executor_index.get(ability_name, set())
        if target_kind in {"Build", "BuildOnUnit"}:
            return "SCV" in executors
        if target_kind in {"BuildInstant", "Train"}:
            return True
        if name in _MACRO_MORPH_UNIT_NAMES and target_kind in {"Morph", "MorphPlace"}:
            return True
    return False


@lru_cache(maxsize=1)
def _names(data_path: str | Path | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = load_database(data_path)
    units, upgrades = build_entity_indexes(data)
    ability_index = build_ability_index(data)
    executor_index = build_executor_index(data, race="Terran")
    unit_names = tuple(
        sorted(
            u["name"]
            for u in units.values()
            if (
                u.get("race") == "Terran"
                and u.get("name")
                and _is_macro_selectable_unit(u["name"], ability_index, executor_index)
            )
        )
    )
    upgrade_names = tuple(
        sorted(u["name"] for u in upgrades.values() if u.get("name") and _is_terran_upgrade(u))
    )
    return unit_names, upgrade_names


def terran_unit_names(data_path: str | Path | None = None) -> list[str]:
    return list(_names(data_path)[0])


def terran_upgrade_names(data_path: str | Path | None = None) -> list[str]:
    return list(_names(data_path)[1])


def is_known_terran_entity(name: str, data_path: str | Path | None = None) -> bool:
    units, upgrades = _names(data_path)
    return name in units or name in upgrades
