"""Rule layer for train/addon/morph executor selection.

The rule layer filters the units/structures that CAN execute an ability right now
(via ``get_available_abilities`` with resource requirements ignored), describes
their status, and pre-computes which candidates are also needed by other pending
actions (conflict hints). The final pick is made by the executor LLM, except when
there is a single candidate (then the rule pick is used directly).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from sc2.ids.ability_id import AbilityId

from SC2_Agent.data_tools import load_database
from SC2_Agent.data_tools.obs_entities import db_name_for_enum
from SC2_Agent.data_tools.sc2_data_common import build_executor_index


@lru_cache(maxsize=1)
def _executor_index() -> dict:
    """``ACTION_NAME -> {executor unit names}`` for Terran."""
    return build_executor_index(load_database(), race="Terran")


async def candidate_executors(ai: Any, ability: AbilityId) -> List[Tuple[Any, str]]:
    """Return ``[(unit, status_text), ...]`` of units that can execute ``ability``.

    Uses ``ignore_resource_requirements=True`` so a producer is not excluded just
    because resources are momentarily short (resources are gated by the scheduler).
    Units building an add-on or otherwise busy are naturally excluded because the
    ability will not appear in their available list.
    """
    candidates: List[Any] = []
    for u in list(ai.units) + list(ai.structures):
        if getattr(u, "build_progress", 1.0) < 1.0:
            continue
        if getattr(u, "is_constructing_scv", False):
            continue
        candidates.append(u)

    if not candidates:
        return []

    try:
        abilities = await ai.get_available_abilities(candidates, ignore_resource_requirements=True)
    except Exception:
        return []

    result: List[Tuple[Any, str]] = []
    for unit, ab_list in zip(candidates, abilities):
        if ability in ab_list:
            result.append((unit, _unit_status_text(unit)))
    return result


def _unit_status_text(unit: Any) -> str:
    parts: List[str] = []
    if getattr(unit, "is_idle", False):
        parts.append("idle")
    else:
        orders = getattr(unit, "orders", None) or []
        if orders:
            try:
                first = orders[0]
                name = first.ability.friendly_name or str(first.ability.id)
                prog = getattr(first, "progress", None)
                if prog is not None:
                    parts.append(f"busy: {name} ({int(prog * 100)}%)")
                else:
                    parts.append(f"busy: {name}")
            except Exception:
                parts.append("busy")
        else:
            parts.append("busy")
    if getattr(unit, "has_techlab", False):
        parts.append("has TechLab")
    elif getattr(unit, "has_reactor", False):
        parts.append("has Reactor")
    elif getattr(unit, "has_add_on", False):
        parts.append("has add-on")
    else:
        # structures that *can* hold an add-on but currently have none
        if unit.type_id.name in ("BARRACKS", "FACTORY", "STARPORT"):
            parts.append("no add-on")
    return ", ".join(parts)


def prompt_tag_aliases(candidates: List[Tuple[Any, str]], modulus: int = 1000) -> Dict[int, int]:
    """Return ``{real_tag: prompt_tag}`` for compact LLM prompts."""
    return {int(unit.tag): int(unit.tag) % modulus for unit, _ in candidates}


def candidates_text(
    candidates: List[Tuple[Any, str]],
    tag_aliases: Optional[Dict[int, int]] = None,
) -> str:
    """Render candidate executors for the LLM prompt."""
    lines: List[str] = []
    for unit, status in candidates:
        name = unit.type_id.name
        tag = tag_aliases.get(unit.tag, unit.tag) if tag_aliases else unit.tag
        lines.append(f"  - tag={tag} {name} [{status}]")
    return "\n".join(lines) or "  (none)"


def candidate_tags(
    candidates: List[Tuple[Any, str]],
    tag_aliases: Optional[Dict[int, int]] = None,
) -> set:
    if tag_aliases:
        return {tag_aliases.get(unit.tag, unit.tag) for unit, _ in candidates}
    return {unit.tag for unit, _ in candidates}


def executor_conflict_hints(
    candidates: List[Tuple[Any, str]],
    pending_action_names: List[str],
) -> str:
    """List pending action names that may conflict with the candidate producers.

    Helps the LLM avoid occupying a producer that a still-pending action needs
    (e.g. a bare Barracks that a pending BUILD_TECHLAB_BARRACKS will require).
    """
    if not candidates or not pending_action_names:
        return ""
    index = _executor_index()
    candidate_executor_names = set()
    for unit, _status in candidates:
        unit_db_name = db_name_for_enum(unit.type_id.name)
        if unit_db_name:
            candidate_executor_names.add(unit_db_name)

    conflict_actions: List[str] = []
    seen = set()
    for action in pending_action_names:
        if action in seen:
            continue
        execs = index.get(action, set())
        if candidate_executor_names.intersection(execs):
            conflict_actions.append(action)
            seen.add(action)
    return "\n".join(f"  - {action}" for action in conflict_actions)
