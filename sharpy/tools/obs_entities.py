"""Map live bot state to DB entity names (from data_ref)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Set

from sharpy.tools.data_ref_loader import DEFAULT_DATA_REF_PATH, get_data_ref_loader


def _norm(name: str) -> str:
    return name.replace("_", "").replace(" ", "").lower()


@lru_cache(maxsize=4)
def _reverse_index(data_ref_path: str) -> Dict[str, str]:
    loader = get_data_ref_loader(data_ref_path)
    loader.load()
    with open(loader.path, "r", encoding="utf-8") as handle:
        import json

        data = json.load(handle)

    index: Dict[str, str] = {}
    for entity in data.get("Unit", []) + data.get("Upgrade", []):
        name = entity.get("name")
        if name:
            index[_norm(name)] = name
    return index


def db_name_for_enum(enum_name: str, data_ref_path: str = DEFAULT_DATA_REF_PATH) -> Optional[str]:
    return _reverse_index(data_ref_path).get(_norm(enum_name))


def _type_db_name(unit: Any, data_ref_path: str) -> Optional[str]:
    try:
        return db_name_for_enum(unit.type_id.name, data_ref_path)
    except Exception:
        return None


def collect_entities(ai: Any, data_ref_path: str = DEFAULT_DATA_REF_PATH) -> Dict[str, List[str]]:
    completed: Set[str] = set()
    in_progress: Set[str] = set()
    pending: Set[str] = set()

    structures = getattr(ai, "structures", None)
    if structures is not None:
        for structure in structures:
            name = _type_db_name(structure, data_ref_path)
            if name is None:
                continue
            if getattr(structure, "build_progress", 1.0) >= 1.0:
                completed.add(name)
            else:
                in_progress.add(name)

    units = getattr(ai, "units", None)
    if units is not None:
        for unit in units:
            name = _type_db_name(unit, data_ref_path)
            if name is None:
                continue
            if getattr(unit, "build_progress", 1.0) >= 1.0:
                completed.add(name)
            else:
                in_progress.add(name)

    state = getattr(ai, "state", None)
    upgrades = getattr(state, "upgrades", None) if state is not None else None
    if upgrades:
        for upgrade in upgrades:
            name = db_name_for_enum(getattr(upgrade, "name", str(upgrade)), data_ref_path)
            if name:
                completed.add(name)

    return {
        "completed": sorted(completed),
        "in_progress": sorted(in_progress),
        "pending": sorted(pending),
    }
