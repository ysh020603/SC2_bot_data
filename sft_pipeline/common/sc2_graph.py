from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_REF = ROOT / "data_ref" / "data_base_add_graph.json"


@lru_cache(maxsize=4)
def load_graph(path: str | None = None) -> dict[str, Any]:
    graph_path = Path(path) if path else DEFAULT_DATA_REF
    with graph_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ability_index(path: str | None = None) -> dict[str, dict[str, Any]]:
    data = load_graph(path)
    return {a["name"]: a for a in data.get("Ability", []) if a.get("name")}


def unit_upgrade_names(race: str = "Terran", path: str | None = None) -> tuple[list[str], list[str]]:
    data = load_graph(path)
    units = sorted(
        u["name"]
        for u in data.get("Unit", [])
        if u.get("race") == race and u.get("name")
    )
    upgrades = sorted(
        u["name"]
        for u in data.get("Upgrade", [])
        if u.get("race") == race and u.get("name")
    )
    return units, upgrades


def action_semantic(action: str, path: str | None = None) -> dict[str, Any] | None:
    entry = ability_index(path).get(action)
    target = entry.get("target") if entry else None
    if not isinstance(target, dict):
        return None
    for kind, payload in target.items():
        if isinstance(payload, dict):
            return {"type": kind, **payload}
    return None


def action_to_named_item(action: str, path: str | None = None) -> dict[str, Any] | None:
    semantic = action_semantic(action, path)
    if not semantic:
        return None
    kind = semantic.get("type")
    if kind in {"Build", "BuildOnUnit", "BuildInstant", "Train", "Morph"}:
        name = semantic.get("produces_name")
    elif kind == "Research":
        name = semantic.get("upgrade_name")
    else:
        name = None
    if not name:
        return None
    return {"name": name, "count": 1}


def aggregate_named_items(actions: list[str], path: str | None = None) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for action in actions:
        item = action_to_named_item(action, path)
        if item:
            counts[item["name"]] += int(item.get("count", 1))
    return [{"name": name, "count": count} for name, count in sorted(counts.items())]


def format_action_counts(actions: list[str]) -> str:
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


def cost_hints(actions: list[str], path: str | None = None) -> str:
    data = load_graph(path)
    units = {u["name"]: u for u in data.get("Unit", []) if u.get("name")}
    upgrades = {u["name"]: u for u in data.get("Upgrade", []) if u.get("name")}
    lines: list[str] = []
    for action in sorted(set(actions)):
        semantic = action_semantic(action, path) or {}
        result = semantic.get("produces_name") or semantic.get("upgrade_name")
        entity = units.get(result) or upgrades.get(result) or {}
        cost = entity.get("cost") or entity
        minerals = int(cost.get("minerals", 0) or 0)
        gas = int(cost.get("gas", 0) or cost.get("vespene", 0) or 0)
        supply = int(entity.get("supply", 0) or 0)
        time = int(entity.get("time", 0) or cost.get("time", 0) or 0)
        lines.append(f"{action}: minerals {minerals}, gas {gas}, supply {supply}, time {time}")
    return "\n".join(lines)


def prereq_hints(actions: list[str], path: str | None = None) -> str:
    data = load_graph(path)
    abilities = ability_index(path)
    produced_by: dict[str, set[str]] = {}
    for action in actions:
        semantic = action_semantic(action, path) or {}
        result = semantic.get("produces_name") or semantic.get("upgrade_name")
        if result:
            produced_by.setdefault(result, set()).add(action)

    lines: list[str] = []
    for action in sorted(set(actions)):
        entry = abilities.get(action) or {}
        chains = entry.get("tech_chain") or []
        chain_text = " ".join(chains[:1])
        for entity, producers in produced_by.items():
            if entity and entity in chain_text and action not in producers:
                for producer in sorted(producers):
                    lines.append(f"{action} should come after {producer} because it needs {entity}.")
    return "\n".join(dict.fromkeys(lines))


def conflict_hints(actions: list[str], path: str | None = None) -> str:
    data = load_graph(path)
    executors: dict[str, set[str]] = {}
    for unit in data.get("Unit", []):
        unit_name = unit.get("name")
        if not unit_name:
            continue
        for ability in unit.get("abilities", []):
            ability_name = ability.get("ability_name")
            if ability_name:
                executors.setdefault(ability_name, set()).add(unit_name)

    lines: list[str] = []
    unique = sorted(set(actions))
    for i, left in enumerate(unique):
        for right in unique[i + 1 :]:
            shared = sorted(executors.get(left, set()).intersection(executors.get(right, set())))
            if shared:
                lines.append(f"{left} and {right} may conflict on producer(s): {', '.join(shared)}.")
    return "\n".join(lines)


def executor_info_for_step(sequence: list[dict[str, Any]], start: int, end: int) -> str:
    lines: list[str] = []
    for entry in sequence[start : end + 1]:
        ctx = entry.get("executor_context")
        if not ctx:
            continue
        ability = ctx.get("ability_name") or entry.get("ability")
        lines.append(f"{ability}: selected tag {ctx.get('selected_tag')} from {ctx.get('candidate_count')} candidates.")
        for cand in ctx.get("candidate_executors", []):
            addon = cand.get("add_on") or "no add-on"
            state = "idle" if cand.get("is_idle") else "busy"
            lines.append(f"  - tag={cand.get('tag')} {cand.get('type')} [{state}, {addon}]")
    return "\n".join(lines)

