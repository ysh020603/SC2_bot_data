"""Resolve Unit/Upgrade standard names to ability names that create them.

Vendored from DATA_TOOLS/tools/entity_to_actions.py (import path adapted for the
``SC2_Agent.data_tools`` package).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import (
        action_result_names,
        build_ability_index,
        build_executor_index,
        load_database,
        target_kind_and_result,
    )
except ImportError:  # pragma: no cover - allow running as a loose script
    from sc2_data_common import (  # type: ignore
        action_result_names,
        build_ability_index,
        build_executor_index,
        load_database,
        target_kind_and_result,
    )


def actions_for_entities(
    names: list[str],
    *,
    data_path: str | Path | None = None,
    executor_race: str | None = "Terran",
) -> dict[str, list[dict[str, Any]]]:
    data = load_database(data_path)
    ability_index = build_ability_index(data)
    executor_index = build_executor_index(data, race=executor_race)
    canonical_names = {
        entity["name"].lower(): entity["name"]
        for group in ("Unit", "Upgrade")
        for entity in data.get(group, [])
        if entity.get("name")
    }
    requested_to_canonical = {
        name: canonical_names.get(name.lower(), name)
        for name in names
    }
    wanted = set(requested_to_canonical.values())
    output: dict[str, list[dict[str, Any]]] = {name: [] for name in names}

    for ability_name, ability in ability_index.items():
        results = action_result_names(ability)
        matched = wanted.intersection(results)
        if not matched:
            continue

        executors = sorted(executor_index.get(ability_name, set()))
        if executor_race and not executors:
            continue

        target_kind, target_result = target_kind_and_result(ability)
        for original_name, canonical_name in requested_to_canonical.items():
            if canonical_name not in matched:
                continue
            output[original_name].append(
                {
                    "entity_name": canonical_name,
                    "ability_name": ability_name,
                    "target_kind": target_kind,
                    "target_result": target_result,
                    "executors": executors,
                }
            )

    for entries in output.values():
        entries.sort(key=lambda item: (item["target_kind"] or "", item["ability_name"]))
    return output
