"""Check whether action prerequisites are present through first tech_chain entries.

Vendored from DATA_TOOLS/tools/check_action_prereqs.py (import path adapted for
the ``SC2_Agent.data_tools`` package) and EXTENDED with
``tech_chain_relations()`` which reports, within a single action list, which
action is a prerequisite of which other action (used as ordering hints for the
ordering LLM).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:  # package import (normal runtime)
    from .sc2_data_common import (
        action_result_names,
        build_ability_index,
        build_executor_index,
        canonical_ability_name,
        canonical_entity_name,
        load_database,
        target_kind_and_result,
    )
except ImportError:  # pragma: no cover - allow running as a loose script
    from sc2_data_common import (  # type: ignore
        action_result_names,
        build_ability_index,
        build_executor_index,
        canonical_ability_name,
        canonical_entity_name,
        load_database,
        target_kind_and_result,
    )


ENTITY_IMPLIES = {
    "CommandCenterFlying": {"CommandCenter"},
    "OrbitalCommand": {"CommandCenter"},
    "OrbitalCommandFlying": {"OrbitalCommand", "CommandCenter"},
    "PlanetaryFortress": {"CommandCenter"},
    "SupplyDepotLowered": {"SupplyDepot"},
    "BarracksFlying": {"Barracks"},
    "FactoryFlying": {"Factory"},
    "StarportFlying": {"Starport"},
    "BarracksTechLab": {"Barracks"},
    "BarracksReactor": {"Barracks"},
    "FactoryTechLab": {"Factory"},
    "FactoryReactor": {"Factory"},
    "StarportTechLab": {"Starport"},
    "StarportReactor": {"Starport"},
    "SiegeTankSieged": {"SiegeTank"},
    "WidowMineBurrowed": {"WidowMine"},
    "HellionTank": {"Hellion"},
    "ThorAP": {"Thor"},
    "VikingAssault": {"VikingFighter"},
    "VikingFighter": {"VikingAssault"},
    "LiberatorAG": {"Liberator"},
}

STEP_RE = re.compile(r"^\s*(?P<entity>.*?)(?:\s*\((?P<action>[^()]*)\))?\s*$")
FINAL_ACTION_RE = re.compile(r"->\s*[^()[\]]+\((?P<action>[^()]*)\)\s*$")


def _entity_closure(entities: set[str]) -> set[str]:
    closed = set(entities)
    changed = True
    while changed:
        changed = False
        for entity in list(closed):
            for implied in ENTITY_IMPLIES.get(entity, set()):
                if implied not in closed:
                    closed.add(implied)
                    changed = True
    return closed


def _parse_step(step_text: str) -> dict[str, str | None]:
    match = STEP_RE.match(step_text)
    if not match:
        return {"entity": step_text.strip(), "action": None}
    entity = match.group("entity").strip()
    action = match.group("action").strip() if match.group("action") else None
    return {"entity": entity, "action": action}


def first_tech_chain_requirements(action_name: str, ability: dict[str, Any]) -> tuple[str | None, list[dict[str, str | None]]]:
    chains = ability.get("tech_chain") or []
    if not chains:
        return None, []

    chain = chains[0]
    for candidate in chains:
        match = FINAL_ACTION_RE.search(candidate)
        if match and match.group("action") == action_name:
            chain = candidate
            break

    body = chain.split(":", 1)[1].strip() if ":" in chain else chain
    branches = re.findall(r"\[([^\]]*)\]", body)
    requirements = []
    for branch in branches:
        steps = [_parse_step(part) for part in branch.split(" -> ") if part.strip()]
        if not steps:
            continue
        requirement = steps[-1]
        if requirement["action"] == action_name:
            continue
        requirements.append(requirement)
    return chain, requirements


def _accepted_alternatives(
    data: dict[str, Any],
    ability_index: dict[str, dict[str, Any]],
    executor_index: dict[str, set[str]],
    action: str,
    requirement: dict[str, str | None],
) -> tuple[str, str | None, set[str]]:
    req_entity = canonical_entity_name(data, requirement["entity"] or "")
    req_action = (
        canonical_ability_name(data, requirement["action"])
        if requirement.get("action")
        else None
    )
    alternatives = {req_entity}
    if req_action:
        alternatives.update(action_result_names(ability_index.get(req_action, {})))
    if req_entity == "TechLab":
        alternatives.update(
            executor
            for executor in executor_index.get(action, set())
            if executor != "TechLab" and executor.endswith("TechLab")
        )
    return req_entity, req_action, alternatives


def _future_producers(
    alternatives: set[str],
    future_actions: list[str],
    action_results_by_index: list[set[str]],
    start_index: int,
) -> list[dict[str, Any]]:
    producers = []
    for future_index in range(start_index + 1, len(future_actions)):
        produced = alternatives.intersection(action_results_by_index[future_index])
        if produced:
            producers.append(
                {
                    "index": future_index,
                    "ability_name": future_actions[future_index],
                    "produces": sorted(produced),
                }
            )
    return producers


def _apply_action_state(
    current_entities: set[str],
    action: str,
    ability: dict[str, Any],
    action_results: set[str],
    executors: set[str],
) -> set[str]:
    next_entities = set(current_entities)
    target_kind, _ = target_kind_and_result(ability)
    if target_kind in {"Morph", "MorphPlace"}:
        for executor in executors:
            next_entities.discard(executor)
    next_entities.update(action_results)
    return next_entities


def check_action_prerequisites(
    entities: list[str],
    actions: list[str],
    *,
    data_path: str | Path | None = None,
) -> dict[str, Any]:
    data = load_database(data_path)
    ability_index = build_ability_index(data)
    executor_index = build_executor_index(data, race=None)

    canonical_entities = [canonical_entity_name(data, entity) for entity in entities]
    canonical_actions = [canonical_ability_name(data, action) for action in actions]

    action_results_by_index: list[set[str]] = []
    for action in canonical_actions:
        ability = ability_index.get(action)
        action_results_by_index.append(set(action_result_names(ability or {})))

    current_entities = set(canonical_entities)
    ordered_reports: list[dict[str, Any]] = []
    legacy_reports: dict[str, Any] = {}
    order_issues: list[dict[str, Any]] = []

    for index, (original_action, action) in enumerate(zip(actions, canonical_actions)):
        ability = ability_index.get(action)
        if ability is None:
            report = {
                "index": index,
                "ability_name": action,
                "known": False,
                "available": False,
                "missing_requirements": [],
                "missing_executors": [],
                "requirements": [],
            }
            ordered_reports.append(report)
            legacy_reports[f"{index}:{original_action}"] = report
            continue

        chain, requirements = first_tech_chain_requirements(action, ability)
        tech_available = _entity_closure(current_entities)
        exact_available = set(current_entities)
        requirement_reports = []
        missing = []
        executor_missing = []

        for requirement in requirements:
            req_entity, req_action, alternatives = _accepted_alternatives(
                data,
                ability_index,
                executor_index,
                action,
                requirement,
            )
            satisfied_by = sorted(alternatives.intersection(tech_available))
            is_satisfied = bool(satisfied_by)
            future = _future_producers(alternatives, canonical_actions, action_results_by_index, index)
            if not is_satisfied:
                missing_item = {
                    "entity_name": req_entity,
                    "source_action": req_action,
                    "accepted_alternatives": sorted(alternatives),
                    "provided_later_by": future,
                    "order_issue": bool(future),
                }
                missing.append(missing_item)
                if future:
                    order_issues.append(
                        {
                            "action_index": index,
                            "ability_name": action,
                            "missing_entity": req_entity,
                            "provided_later_by": future,
                        }
                    )
            requirement_reports.append(
                {
                    "entity_name": req_entity,
                    "source_action": req_action,
                    "accepted_alternatives": sorted(alternatives),
                    "satisfied": is_satisfied,
                    "satisfied_by": satisfied_by,
                    "provided_later_by": future,
                    "order_issue": (not is_satisfied and bool(future)),
                }
            )

        executors = {
            executor
            for executor in executor_index.get(action, set())
            if executor != "TechLab"
        }
        if executors:
            present_executors = sorted(executors.intersection(exact_available))
            if not present_executors:
                future = _future_producers(executors, canonical_actions, action_results_by_index, index)
                executor_missing = [
                    {
                        "accepted_executors": sorted(executors),
                        "provided_later_by": future,
                        "order_issue": bool(future),
                    }
                ]
                if future:
                    order_issues.append(
                        {
                            "action_index": index,
                            "ability_name": action,
                            "missing_executor": sorted(executors),
                            "provided_later_by": future,
                        }
                    )
        else:
            present_executors = []

        available_now = not missing and not executor_missing
        report = {
            "index": index,
            "ability_name": action,
            "known": True,
            "available": available_now,
            "available_entities_before": sorted(current_entities),
            "tech_chain_used": chain,
            "requirements": requirement_reports,
            "missing_requirements": missing,
            "accepted_executors": sorted(executors),
            "satisfied_executors": present_executors,
            "missing_executors": executor_missing,
        }
        ordered_reports.append(report)
        legacy_reports[f"{index}:{original_action}"] = report

        if available_now:
            current_entities = _apply_action_state(
                current_entities,
                action,
                ability,
                action_results_by_index[index],
                executors,
            )

    return {
        "all_available": all(report.get("available", False) for report in ordered_reports),
        "has_order_issue": bool(order_issues),
        "order_issues": order_issues,
        "entities": canonical_entities,
        "actions": canonical_actions,
        "final_available_entities": sorted(current_entities),
        "final_inferred_available_entities": sorted(_entity_closure(current_entities)),
        "action_results": [
            {
                "index": index,
                "ability_name": action,
                "results": sorted(results),
            }
            for index, (action, results) in enumerate(zip(canonical_actions, action_results_by_index))
        ],
        "ordered_reports": ordered_reports,
        "reports": legacy_reports,
        "note": "Actions are checked in list order. A later action result can trigger an order_issue but cannot satisfy an earlier action.",
    }


# ======================================================================
# EXTENSION: tech-chain relations within an action list
# ======================================================================


def tech_chain_relations(
    actions: list[str],
    *,
    data_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Report prerequisite relations *between actions in the same list*.

    For each action, look at its first-tech-chain requirements and check whether
    another action in the same list produces the required entity. Order in the
    input list is irrelevant here; the goal is purely to tell the ordering LLM
    "action A must come before action B because B needs what A produces".

    :return: list of ``{"action": B, "depends_on": A, "via_entity": E}`` dicts.
    """
    data = load_database(data_path)
    ability_index = build_ability_index(data)
    executor_index = build_executor_index(data, race=None)

    canonical_actions = [canonical_ability_name(data, a) for a in actions]

    # entity -> set of actions in the list that produce it
    producers: dict[str, set[str]] = {}
    for action in canonical_actions:
        ability = ability_index.get(action)
        for result in action_result_names(ability or {}):
            producers.setdefault(result, set()).add(action)

    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for action in canonical_actions:
        ability = ability_index.get(action)
        if ability is None:
            continue
        _, requirements = first_tech_chain_requirements(action, ability)
        for requirement in requirements:
            _req_entity, _req_action, alternatives = _accepted_alternatives(
                data, ability_index, executor_index, action, requirement
            )
            for alt in alternatives:
                for producer_action in producers.get(alt, set()):
                    if producer_action == action:
                        continue
                    key = (action, producer_action, alt)
                    if key in seen:
                        continue
                    seen.add(key)
                    relations.append(
                        {
                            "action": action,
                            "depends_on": producer_action,
                            "via_entity": alt,
                        }
                    )
    return relations
