"""Detect possible queue/resource conflicts between Terran ability actions.

Vendored from DATA_TOOLS/tools/detect_action_conflicts.py (import path adapted
for the ``SC2_Agent.data_tools`` package).
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import (
        build_ability_index,
        build_executor_index,
        is_queue_action,
        load_database,
        normalized_executor_resources,
        target_kind_and_result,
    )
except ImportError:  # pragma: no cover - allow running as a loose script
    from sc2_data_common import (  # type: ignore
        build_ability_index,
        build_executor_index,
        is_queue_action,
        load_database,
        normalized_executor_resources,
        target_kind_and_result,
    )


def detect_action_conflicts(
    actions: list[str],
    *,
    data_path: str | Path | None = None,
    executor_race: str | None = "Terran",
    ignore_scv_builds: bool = True,
) -> dict[str, Any]:
    data = load_database(data_path)
    ability_index = build_ability_index(data)
    executor_index = build_executor_index(data, race=executor_race)

    action_infos: dict[str, dict[str, Any]] = {}
    for action in actions:
        ability = ability_index.get(action)
        target_kind, target_result = target_kind_and_result(ability or {})
        executors = executor_index.get(action, set())
        queue_action = is_queue_action(action, ability)
        resources = (
            normalized_executor_resources(
                action,
                ability,
                executors,
                ignore_scv_builds=ignore_scv_builds,
            )
            if queue_action
            else set()
        )
        action_infos[action] = {
            "known": ability is not None,
            "target_kind": target_kind,
            "target_result": target_result,
            "queue_action": queue_action,
            "executors": sorted(executors),
            "resources": sorted(resources),
        }

    conflicts = []
    for left, right in itertools.combinations(actions, 2):
        shared = sorted(set(action_infos[left]["resources"]).intersection(action_infos[right]["resources"]))
        if not shared:
            continue
        conflicts.append(
            {
                "actions": [left, right],
                "shared_resources": shared,
                "reason": "Both actions may occupy the same producer/research queue resource.",
            }
        )

    return {
        "has_conflict": bool(conflicts),
        "conflicts": conflicts,
        "actions": action_infos,
        "options": {
            "executor_race": executor_race,
            "ignore_scv_builds": ignore_scv_builds,
        },
    }
