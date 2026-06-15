"""Adapt sharpy / python-sc2 live game state into DB entity-name lists.

``check_action_prereqs`` only understands *completed* canonical entity names from
``data_base_add_graph.json``. To use it at runtime we must translate the bot's
live state (``BotAI``) into those names, while also distinguishing three states:

* ``completed`` – the entity physically exists and is finished (ready).
* ``in_progress`` – under construction / morphing / researching right now.
* ``pending`` – queued or otherwise "on the way" (e.g. addon being built).

The mapping from ``UnitTypeId`` / ``UpgradeId`` enum names to the DB's PascalCase
names is done by normalising both sides (lower-case, strip separators) and using
a reverse index built once from the database.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import build_entity_indexes, load_database
except ImportError:  # pragma: no cover
    from sc2_data_common import build_entity_indexes, load_database  # type: ignore


def _norm(name: str) -> str:
    return name.replace("_", "").replace(" ", "").lower()


@lru_cache(maxsize=1)
def _reverse_index() -> dict[str, str]:
    """``normalised name -> canonical DB name`` for every Unit and Upgrade."""
    data = load_database()
    units, upgrades = build_entity_indexes(data)
    index: dict[str, str] = {}
    for entity in [*units.values(), *upgrades.values()]:
        name = entity.get("name")
        if name:
            index[_norm(name)] = name
    return index


def db_name_for_enum(enum_name: str) -> str | None:
    """Map a ``UnitTypeId`` / ``UpgradeId`` ``.name`` to a canonical DB name."""
    return _reverse_index().get(_norm(enum_name))


def _type_db_name(unit: Any) -> str | None:
    try:
        return db_name_for_enum(unit.type_id.name)
    except Exception:
        return None


def collect_entities(ai: Any) -> dict[str, list[str]]:
    """Return ``{"completed": [...], "in_progress": [...], "pending": [...]}``.

    ``completed`` is what ``check_action_prereqs`` should treat as available now.
    The other two buckets let the scheduler know a prerequisite is "coming" so it
    does not wrongly insert a duplicate prerequisite action.
    """
    completed: set[str] = set()
    in_progress: set[str] = set()
    pending: set[str] = set()

    # --- structures (ready vs under construction) ---
    structures = getattr(ai, "structures", None)
    if structures is not None:
        for st in structures:
            name = _type_db_name(st)
            if name is None:
                continue
            if getattr(st, "build_progress", 1.0) >= 1.0:
                completed.add(name)
                # add-on awareness
                if getattr(st, "has_techlab", False):
                    in_progress.discard(name)
                # a structure currently building an add-on
                if getattr(st, "is_active", False) and not getattr(st, "is_idle", True):
                    pass
            else:
                in_progress.add(name)

    # --- non-structure units (workers + army) ---
    units = getattr(ai, "units", None)
    if units is not None:
        for u in units:
            name = _type_db_name(u)
            if name is None:
                continue
            if getattr(u, "build_progress", 1.0) >= 1.0:
                completed.add(name)
            else:
                in_progress.add(name)

    # --- completed upgrades ---
    state = getattr(ai, "state", None)
    upgrades = getattr(state, "upgrades", None) if state is not None else None
    if upgrades:
        for up in upgrades:
            name = db_name_for_enum(getattr(up, "name", str(up)))
            if name:
                completed.add(name)

    return {
        "completed": sorted(completed),
        "in_progress": sorted(in_progress),
        "pending": sorted(pending),
    }


def obs_entities(ai: Any) -> list[str]:
    """Completed-only entity names (the input ``check_action_prereqs`` expects)."""
    return collect_entities(ai)["completed"]
