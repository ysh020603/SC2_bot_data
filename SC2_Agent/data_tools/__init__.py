"""Self-contained SC2 tech-graph data layer (vendored from DATA_TOOLS + extensions).

This package bundles ``data_base_add_graph.json`` and the original DATA_TOOLS
helpers, plus sharpy-specific extensions:

* :mod:`sc2_data_common`     – database loader/index helpers (cached).
* :mod:`entity_to_actions`   – Unit/Upgrade name -> producing Action(s).
* :mod:`action_cost`         – Action -> minerals/gas/supply/time.
* :mod:`detect_action_conflicts` – producer/queue conflicts between actions.
* :mod:`check_action_prereqs`    – ordered prereq check + ``tech_chain_relations``.
* :mod:`terran_names`        – canonical Terran name lists + alias map.
* :mod:`obs_entities`        – live bot state -> DB entity names (3 states).
* :mod:`prereq_runtime`      – runtime prereq / tech-chain reasoning.
* :mod:`supply_planner`      – insert supply depots into an ordered action list.
"""

from __future__ import annotations

from .action_cost import cost_for_action
from .check_action_prereqs import check_action_prerequisites, tech_chain_relations
from .detect_action_conflicts import detect_action_conflicts
from .entity_to_actions import actions_for_entities
from .obs_entities import collect_entities, db_name_for_enum, obs_entities
from .prereq_runtime import (
    chain_in_progress,
    gap_fill_actions,
    is_available_now,
    missing_chain,
)
from .sc2_data_common import (
    canonical_ability_name,
    canonical_entity_name,
    load_database,
)
from .supply_planner import SUPPLY_DEPOT_ACTION
from .supply_planner import plan as plan_supply
from .supply_planner import plan_with_trace as plan_supply_with_trace
from .terran_names import (
    ALIAS_MAP,
    is_known_terran_entity,
    resolve_alias,
    terran_unit_names,
    terran_upgrade_names,
)

__all__ = [
    "cost_for_action",
    "check_action_prerequisites",
    "tech_chain_relations",
    "detect_action_conflicts",
    "actions_for_entities",
    "collect_entities",
    "db_name_for_enum",
    "obs_entities",
    "chain_in_progress",
    "gap_fill_actions",
    "is_available_now",
    "missing_chain",
    "canonical_ability_name",
    "canonical_entity_name",
    "load_database",
    "SUPPLY_DEPOT_ACTION",
    "plan_supply",
    "plan_supply_with_trace",
    "ALIAS_MAP",
    "is_known_terran_entity",
    "resolve_alias",
    "terran_unit_names",
    "terran_upgrade_names",
]
