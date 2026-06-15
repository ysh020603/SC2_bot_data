"""Canonical Terran Unit/Upgrade name lists + alias map.

The naming LLM (stage 2) maps free-form natural-language increments onto the
canonical entity names used by ``data_base_add_graph.json``. This module exports:

* ``terran_unit_names()``    – canonical Terran Unit names (race == "Terran").
* ``terran_upgrade_names()`` – canonical Terran Upgrade names (filtered by the
  Terran action prefixes appearing in their ``tech_chain``).
* ``ALIAS_MAP``              – common UI/alias spellings -> canonical DB name.
* ``resolve_alias()``        – normalise one name through the alias map + DB.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import build_entity_indexes, canonical_entity_name, load_database
except ImportError:  # pragma: no cover
    from sc2_data_common import build_entity_indexes, canonical_entity_name, load_database  # type: ignore


#: Action-name fragments that mark an Upgrade as Terran-researchable.
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
    "RESEARCH_",
    "BUILD_TECHLAB",
    "BUILD_REACTOR",
    "UPGRADETO",
    "LIFT_",
    "LAND_",
    "MORPH_",
    "HISECAUTOTRACKING",
    "TERRAN",
)

#: UI / common spelling -> canonical database name. Lower-cased keys.
ALIAS_MAP: dict[str, str] = {
    "combat shield": "ShieldWall",
    "combatshield": "ShieldWall",
    "shield wall": "ShieldWall",
    "stim": "Stimpack",
    "stim pack": "Stimpack",
    "stimpack": "Stimpack",
    "concussive shells": "PunisherGrenades",
    "concussiveshells": "PunisherGrenades",
    "punisher grenades": "PunisherGrenades",
    "infernal pre-igniter": "HighCapacityBarrels",
    "infernal preigniter": "HighCapacityBarrels",
    "drilling claws": "DrillClaws",
    "mag-field accelerator": "MagFieldLaunchers",
    "magfield accelerator": "MagFieldLaunchers",
    "smart servos": "SmartServos",
    "cloaking field": "BansheeCloak",
    "banshee cloak": "BansheeCloak",
    "hyperflight rotors": "BansheeSpeed",
    "corvid reactor": "RavenCorvidReactor",
    "advanced ballistics": "LiberatorAGRangeUpgrade",
    "yamato cannon": "BattlecruiserEnableSpecializations",
    "hi-sec auto tracking": "HiSecAutoTracking",
    "neosteel armor": "TerranBuildingArmor",
    "neosteel frame": "TerranBuildingArmor",
    "personal cloaking": "PersonalCloaking",
    "tech lab": "BarracksTechLab",
    "reactor": "BarracksReactor",
    "orbital": "OrbitalCommand",
    "orbital command": "OrbitalCommand",
    "planetary": "PlanetaryFortress",
    "planetary fortress": "PlanetaryFortress",
    "cc": "CommandCenter",
    "command center": "CommandCenter",
    "rax": "Barracks",
    "depot": "SupplyDepot",
    "supply depot": "SupplyDepot",
    "refinery": "Refinery",
    "ebay": "EngineeringBay",
    "engineering bay": "EngineeringBay",
    "scv": "SCV",
}


def _is_terran_upgrade(upgrade: dict[str, Any]) -> bool:
    chains = " ".join(upgrade.get("tech_chain") or [])
    if not chains:
        return False
    return any(prefix in chains for prefix in _TERRAN_ACTION_PREFIXES)


@lru_cache(maxsize=1)
def _names(data_path: str | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = load_database(data_path)
    units, upgrades = build_entity_indexes(data)
    unit_names = tuple(
        sorted(u["name"] for u in units.values() if u.get("race") == "Terran" and u.get("name"))
    )
    upgrade_names = tuple(
        sorted(u["name"] for u in upgrades.values() if u.get("name") and _is_terran_upgrade(u))
    )
    return unit_names, upgrade_names


def terran_unit_names(data_path: str | None = None) -> list[str]:
    return list(_names(data_path)[0])


def terran_upgrade_names(data_path: str | None = None) -> list[str]:
    return list(_names(data_path)[1])


def resolve_alias(name: str, data_path: str | None = None) -> str:
    """Normalise ``name`` through the alias map, then the database canonicaliser."""
    if not name:
        return name
    alias = ALIAS_MAP.get(name.strip().lower())
    if alias:
        return alias
    data = load_database(data_path)
    return canonical_entity_name(data, name.strip())


def is_known_terran_entity(name: str, data_path: str | None = None) -> bool:
    units, upgrades = _names(data_path)
    return name in units or name in upgrades
