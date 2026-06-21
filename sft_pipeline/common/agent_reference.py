from __future__ import annotations

import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "SC2-Agent-260510"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from SC2_Agent.data_tools.action_cost import cost_for_action  # type: ignore  # noqa: E402
from SC2_Agent.data_tools.check_action_prereqs import (  # type: ignore  # noqa: E402
    check_action_prerequisites,
    tech_chain_relations,
)
from SC2_Agent.data_tools.detect_action_conflicts import detect_action_conflicts  # type: ignore  # noqa: E402
from SC2_Agent.data_tools.entity_to_actions import actions_for_entities  # type: ignore  # noqa: E402
from SC2_Agent.data_tools.obs_entities import db_name_for_enum  # type: ignore  # noqa: E402
from SC2_Agent.data_tools.sc2_data_common import (  # type: ignore  # noqa: E402
    build_executor_index,
    load_database,
)
from SC2_Agent.data_tools.terran_names import (  # type: ignore  # noqa: E402
    terran_unit_names,
    terran_upgrade_names,
)
from SC2_Agent.executor_agent import build_executor_messages  # type: ignore  # noqa: E402
from SC2_Agent.naming_agent import build_naming_messages  # type: ignore  # noqa: E402
from SC2_Agent.ordering_agent import build_ordering_messages  # type: ignore  # noqa: E402


SUMMARY_RE = re.compile(r"^# Summary\s*(?P<body>.*?)(?:^# Details\s*|\Z)", re.S | re.M)


def strategy_summary_from_md(path: str | None) -> str:
    if not path:
        return ""
    md_path = Path(path)
    if not md_path.exists():
        return ""
    text = md_path.read_text(encoding="utf-8")
    match = SUMMARY_RE.search(text)
    if not match:
        return ""
    return match.group("body").strip()


def completed_entities_from_obs(obs: dict[str, Any]) -> list[str]:
    structured = obs.get("structured") or {}
    own_forces = structured.get("own_forces") or {}
    completed = own_forces.get("completed") or {}
    upgrades = structured.get("upgrades") or []
    entities: set[str] = set()

    if isinstance(completed, dict):
        for raw_name, count in completed.items():
            try:
                if int(count) <= 0:
                    continue
            except Exception:
                pass
            mapped = db_name_for_enum(str(raw_name)) or _pascal_from_obs_name(str(raw_name))
            if mapped:
                entities.add(mapped)

    if isinstance(upgrades, list):
        for raw_name in upgrades:
            mapped = db_name_for_enum(str(raw_name)) or str(raw_name)
            if mapped:
                entities.add(mapped)

    return sorted(entities)


def _pascal_from_obs_name(name: str) -> str:
    pieces = [p for p in re.split(r"[_\s]+", name.lower()) if p]
    return "".join(p.capitalize() for p in pieces)


def primary_action_for_entity(entity_name: str) -> str | None:
    mapping = actions_for_entities([entity_name], executor_race="Terran")
    entries = mapping.get(entity_name) or []
    if not entries:
        return None

    def rank(entry: dict[str, Any]) -> tuple[int, str]:
        kind = entry.get("target_kind") or ""
        order = {
            "Build": 0,
            "BuildOnUnit": 0,
            "BuildInstant": 0,
            "Train": 1,
            "Research": 2,
            "Morph": 3,
        }
        return order.get(kind, 9), entry.get("ability_name") or ""

    return sorted(entries, key=rank)[0].get("ability_name")


def action_counts_text(actions: list[str]) -> str:
    counts = Counter(actions)
    seen: set[str] = set()
    lines: list[str] = []
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        count = counts[action]
        lines.append(f"{action} x {count}" if count > 1 else action)
    return "\n".join(lines)


def build_prereq_hints(entities: list[str], actions: list[str]) -> str:
    lines: list[str] = []
    try:
        result = check_action_prerequisites(entities, actions)
        for rep in result.get("ordered_reports", []):
            missing = rep.get("missing_requirements") or []
            if missing:
                names = ", ".join(m.get("entity_name", "?") for m in missing)
                lines.append(f"  - {rep['ability_name']} still missing: {names}")
    except Exception:
        pass
    try:
        for rel in tech_chain_relations(actions, available_entities=entities):
            lines.append(
                f"  - {rel['action']} requires {rel['depends_on']} first (via {rel['via_entity']})"
            )
    except Exception:
        pass
    return "\n".join(lines)


def build_conflict_hints(actions: list[str]) -> str:
    lines: list[str] = []
    try:
        result = detect_action_conflicts(actions)
        for conflict in result.get("conflicts", []):
            left, right = conflict["actions"]
            shared = ", ".join(conflict.get("shared_resources", []))
            lines.append(f"  - {left} and {right} share producer(s): {shared}")
    except Exception:
        pass
    return "\n".join(lines)


def build_cost_hints(actions: list[str]) -> str:
    lines: list[str] = []
    for action in actions:
        try:
            info = cost_for_action(action)
            cost = info.get("cost") or {}
            frames = float(cost.get("time", 0) or 0)
            seconds = frames / 22.4 if frames else 0.0
            lines.append(
                f"  - {action}: minerals {cost.get('minerals', 0)}, gas {cost.get('gas', 0)}, "
                f"supply {cost.get('supply', 0)}, ~{seconds:.0f}s"
            )
        except Exception:
            pass
    return "\n".join(lines)


def executor_conflict_hints_for_candidate_types(
    candidate_types: list[str],
    pending_action_names: list[str],
) -> str:
    if not candidate_types or not pending_action_names:
        return ""
    try:
        index = build_executor_index(load_database(), race="Terran")
    except Exception:
        return ""

    candidate_executor_names: set[str] = set()
    for unit_type in candidate_types:
        mapped = db_name_for_enum(str(unit_type)) or _pascal_from_obs_name(str(unit_type))
        if mapped:
            candidate_executor_names.add(mapped)

    lines: list[str] = []
    seen: set[str] = set()
    for action in pending_action_names:
        if action in seen:
            continue
        executors = index.get(action, set())
        if candidate_executor_names.intersection(executors):
            lines.append(f"  - {action}")
            seen.add(action)
    return "\n".join(lines)


def prompt_tag_aliases(candidate_tags: list[int], modulus: int = 1000) -> dict[int, int]:
    return {int(tag): int(tag) % modulus for tag in candidate_tags}


def reverse_prompt_tag_map(candidate_tags: list[int], modulus: int = 1000) -> dict[int, int]:
    aliases = prompt_tag_aliases(candidate_tags, modulus=modulus)
    reverse: dict[int, int] = {}
    for real_tag, prompt_tag in aliases.items():
        if prompt_tag in reverse:
            return {}
        reverse[prompt_tag] = real_tag
    return reverse


@lru_cache(maxsize=1)
def canonical_terran_names() -> tuple[list[str], list[str]]:
    return terran_unit_names(), terran_upgrade_names()
