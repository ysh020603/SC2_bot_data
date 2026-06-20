"""Shared helpers for tools built on data_base_add_graph.json.

Vendored into sharpy-sc2 from DATA_TOOLS/tools/sc2_data_common.py. The only
change versus the original is ``DEFAULT_DATA_PATH``: the JSON database now lives
next to this module (inside ``SC2_Agent/data_tools/``) so the whole pipeline is
self-contained within the sharpy-sc2 repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_PATH = Path(__file__).resolve().parent / "data_base_add_graph.json"

QUEUE_TARGET_KINDS = {
    "Build",
    "BuildOnUnit",
    "BuildInstant",
    "Train",
    "Research",
    "Morph",
}

ADDON_EXECUTOR_TO_HOST = {
    "BarracksTechLab": "Barracks",
    "BarracksReactor": "Barracks",
    "FactoryTechLab": "Factory",
    "FactoryReactor": "Factory",
    "StarportTechLab": "Starport",
    "StarportReactor": "Starport",
}

GENERIC_TECHLAB_HOST_ACTIONS = {
    "BARRACKSTECHLABRESEARCH_STIMPACK": "Barracks",
    "RESEARCH_COMBATSHIELD": "Barracks",
    "RESEARCH_CONCUSSIVESHELLS": "Barracks",
    "RESEARCH_INFERNALPREIGNITER": "Factory",
    "RESEARCH_CYCLONELOCKONDAMAGE": "Factory",
    "RESEARCH_DRILLINGCLAWS": "Factory",
    "RESEARCH_SMARTSERVOS": "Factory",
    "RESEARCH_BANSHEECLOAKINGFIELD": "Starport",
    "RESEARCH_BANSHEEHYPERFLIGHTROTORS": "Starport",
    "RESEARCH_RAVENCORVIDREACTOR": "Starport",
    "STARPORTTECHLABRESEARCH_RESEARCHRAVENINTERFERENCEMATRIX": "Starport",
}

#: Module-level cache so the ~2 MB database is parsed at most once per process.
_DATABASE_CACHE: dict[str, Any] | None = None


def load_database(data_path: str | Path | None = None) -> dict[str, Any]:
    """Load the SC2 tech-graph database, caching the default path in-process."""
    global _DATABASE_CACHE
    if data_path is None:
        if _DATABASE_CACHE is None:
            with DEFAULT_DATA_PATH.open("r", encoding="utf-8") as f:
                _DATABASE_CACHE = json.load(f)
        return _DATABASE_CACHE
    path = Path(data_path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def target_kind_and_result(ability: dict[str, Any]) -> tuple[str | None, str | None]:
    target = ability.get("target")
    if isinstance(target, str):
        return target, None
    if isinstance(target, dict) and target:
        kind, payload = next(iter(target.items()))
        if isinstance(payload, dict):
            return kind, payload.get("produces_name") or payload.get("upgrade_name")
        return kind, None
    return None, None


def action_result_names(ability: dict[str, Any]) -> list[str]:
    result = []
    for relation in ability.get("relations", []):
        if relation.get("relation") == "action_result" and relation.get("object_name"):
            result.append(relation["object_name"])
    _, target_result = target_kind_and_result(ability)
    if target_result and target_result not in result:
        result.append(target_result)
    return result


def build_ability_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {ability["name"]: ability for ability in data.get("Ability", [])}


def build_executor_index(data: dict[str, Any], race: str | None = "Terran") -> dict[str, set[str]]:
    executors: dict[str, set[str]] = {}
    for unit in data.get("Unit", []):
        if race and unit.get("race") != race:
            continue
        for ability_ref in unit.get("abilities", []):
            ability_name = ability_ref.get("ability_name")
            if not ability_name:
                continue
            executors.setdefault(ability_name, set()).add(unit["name"])
    return executors


def build_entity_indexes(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    units = {unit["name"]: unit for unit in data.get("Unit", [])}
    upgrades = {upgrade["name"]: upgrade for upgrade in data.get("Upgrade", [])}
    return units, upgrades


def canonical_entity_name(data: dict[str, Any], entity_name: str) -> str:
    units, upgrades = build_entity_indexes(data)
    entity_names = {
        entity["name"].lower(): entity["name"]
        for entity in [*units.values(), *upgrades.values()]
        if entity.get("name")
    }
    return entity_names.get(entity_name.lower(), entity_name)


def canonical_ability_name(data: dict[str, Any], action_name: str) -> str:
    ability_names = {
        ability["name"].lower(): ability["name"]
        for ability in data.get("Ability", [])
        if ability.get("name")
    }
    return ability_names.get(action_name.lower(), action_name)


def is_queue_action(ability_name: str, ability: dict[str, Any] | None) -> bool:
    kind = target_kind_and_result(ability or {})[0]
    if kind in QUEUE_TARGET_KINDS:
        return True
    return ability_name == "BUILD_NUKE" or ability_name.startswith(("LIFT_", "LAND_"))


def normalized_executor_resources(
    ability_name: str,
    ability: dict[str, Any] | None,
    executors: set[str],
    *,
    ignore_scv_builds: bool = True,
) -> set[str]:
    kind = target_kind_and_result(ability or {})[0]
    resources: set[str] = set()
    for executor in executors:
        if ignore_scv_builds and executor == "SCV" and kind in {"Build", "BuildOnUnit"}:
            continue
        if executor == "TechLab":
            continue
        resources.add(ADDON_EXECUTOR_TO_HOST.get(executor, executor))

    host = GENERIC_TECHLAB_HOST_ACTIONS.get(ability_name)
    if host:
        resources.add(host)

    return resources
