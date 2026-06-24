from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.templates import dataset_info_fragment, sharegpt_sample
from sft_pipeline.common.agent_reference import (
    build_conflict_hints,
    build_cost_hints,
    build_executor_messages,
    build_naming_messages,
    build_ordering_messages,
    build_prereq_hints,
    canonical_terran_names,
    completed_entities_from_obs,
    executor_conflict_hints_for_candidate_types,
    reverse_prompt_tag_map,
    strategy_summary_from_md,
)
from sft_pipeline.common.io import read_jsonl, write_json
from sft_pipeline.common.sc2_graph import aggregate_named_items


def _seed_for(text: str, variant: int = 0) -> int:
    digest = hashlib.sha256(f"{text}:{variant}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _shuffle_actions(actions: list[str], sample_id: str, variant: int) -> list[str]:
    shuffled = list(actions)
    rng = random.Random(_seed_for(sample_id, variant))
    for _ in range(8):
        rng.shuffle(shuffled)
        if shuffled != actions:
            break
    return shuffled


def _obs_text(row: dict[str, Any]) -> str:
    obs = row.get("obs_at_step_start") or {}
    return obs.get("text") or ""


def _strategy_summary(row: dict[str, Any]) -> str:
    return row.get("strategy_summary") or strategy_summary_from_md(row.get("md_path"))


def _step_text(row: dict[str, Any]) -> str:
    return row.get("step_text_v8") or row.get("step_text_v7") or row.get("step_text_v6") or ""


def _agent_pair(messages: list[dict[str, str]]) -> tuple[str, str]:
    return messages[0]["content"], messages[1]["content"]


def build_naming_samples(rows: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    samples: list[dict[str, Any]] = []
    qa = {"total": 0, "kept": 0, "dropped_empty_answer": 0}
    units, upgrades = canonical_terran_names()
    for row in rows:
        qa["total"] += 1
        actions = list(row.get("ordered_actions") or [])
        items = aggregate_named_items(actions)
        if not items:
            qa["dropped_empty_answer"] += 1
            continue
        system, user = _agent_pair(
            build_naming_messages(
                race="terran",
                plan_text=_step_text(row),
                terran_unit_names=units,
                terran_upgrade_names=upgrades,
                obs_text=_obs_text(row),
                strategy_summary=_strategy_summary(row),
            )
        )
        answer = {"items": items}
        samples.append(sharegpt_sample("naming", mode, user, answer, system=system))
        qa["kept"] += 1
    return samples, qa


def build_ordering_samples(
    rows: list[dict[str, Any]],
    mode: str,
    shuffle_variants: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    samples: list[dict[str, Any]] = []
    qa = {
        "total": 0,
        "kept": 0,
        "dropped_short": 0,
        "dropped_shuffle_same": 0,
        "dropped_counter_mismatch": 0,
    }
    for row in rows:
        ordered = list(row.get("ordered_actions") or [])
        if len(ordered) < 2:
            qa["dropped_short"] += 1
            continue
        actions = list(dict.fromkeys(ordered))
        entities = completed_entities_from_obs(row.get("obs_at_step_start") or {})
        prereq = build_prereq_hints(entities, actions)
        conflicts = build_conflict_hints(actions)
        costs = build_cost_hints(actions)

        for variant in range(shuffle_variants):
            qa["total"] += 1
            shuffled = _shuffle_actions(ordered, row.get("sample_id", ""), variant)
            if shuffled == ordered:
                qa["dropped_shuffle_same"] += 1
                continue
            if Counter(shuffled) != Counter(ordered):
                qa["dropped_counter_mismatch"] += 1
                continue
            system, user = _agent_pair(
                build_ordering_messages(
                    race="terran",
                    actions=shuffled,
                    obs_text=_obs_text(row),
                    prereq_hints=prereq,
                    conflict_hints=conflicts,
                    cost_hints=costs,
                    strategy_step_text=_step_text(row),
                    strategy_summary=_strategy_summary(row),
                )
            )
            answer = {"ordered_actions": ordered}
            samples.append(sharegpt_sample("ordering", mode, user, answer, system=system))
            qa["kept"] += 1
    return samples, qa


def _candidate_status(cand: dict[str, Any]) -> str:
    parts: list[str] = []
    if cand.get("is_idle"):
        parts.append("idle")
    else:
        orders = cand.get("orders") or []
        if orders:
            first = orders[0]
            ability = first.get("ability") or "busy"
            progress = first.get("progress")
            if progress is not None:
                try:
                    parts.append(f"busy: {ability} ({int(float(progress) * 100)}%)")
                except Exception:
                    parts.append(f"busy: {ability}")
            else:
                parts.append(f"busy: {ability}")
        else:
            parts.append("busy")

    addon = cand.get("add_on")
    if addon:
        addon_name = str(addon).upper()
        if "TECHLAB" in addon_name:
            parts.append("has TechLab")
        elif "REACTOR" in addon_name:
            parts.append("has Reactor")
        else:
            parts.append("has add-on")
    elif cand.get("type") in {"BARRACKS", "FACTORY", "STARPORT"}:
        parts.append("no add-on")
    return ", ".join(parts)


def _candidate_units_text(candidates: list[dict[str, Any]], tag_map: dict[int, int]) -> str:
    reverse = {real: prompt for prompt, real in tag_map.items()}
    lines: list[str] = []
    for cand in candidates:
        real_tag = int(cand.get("tag"))
        prompt_tag = reverse.get(real_tag, real_tag)
        lines.append(f"  - tag={prompt_tag} {cand.get('type')} [{_candidate_status(cand)}]")
    return "\n".join(lines) or "  (none)"


def _remaining_actions_after(row: dict[str, Any], action_index: int) -> list[str]:
    entries = row.get("obs_at_each_action") or []
    remaining: list[str] = []
    for item in entries[action_index + 1 :]:
        ability = item.get("ability")
        if isinstance(ability, str) and ability:
            remaining.append(ability)
    return remaining


def _pending_summary_from_actions(actions: list[str], limit: int = 40) -> str:
    if not actions:
        return ""
    counts = Counter(actions)
    seen: set[str] = set()
    lines: list[str] = []
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        count = int(counts[action])
        if count > 1:
            lines.append(f"  - {action} x{count} (0/{count} issued) [PENDING]")
        else:
            lines.append(f"  - {action} [PENDING]")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def build_executor_samples(rows: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    samples: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    qa = {
        "total": 0,
        "kept": 0,
        "dropped_no_context": 0,
        "dropped_single_candidate": 0,
        "dropped_bad_selected_tag": 0,
        "dropped_tag_alias_collision": 0,
    }
    for row in rows:
        for action_index, action_obs in enumerate(row.get("obs_at_each_action") or []):
            ctx = action_obs.get("executor_context") or {}
            if not ctx:
                qa["dropped_no_context"] += 1
                continue
            qa["total"] += 1
            candidates = ctx.get("candidate_executors") or []
            if len(candidates) <= 1:
                qa["dropped_single_candidate"] += 1
                continue
            selected = ctx.get("selected_tag")
            legal = {cand.get("tag") for cand in candidates}
            if selected not in legal:
                qa["dropped_bad_selected_tag"] += 1
                continue
            tag_map = reverse_prompt_tag_map([int(cand.get("tag")) for cand in candidates])
            if not tag_map:
                qa["dropped_tag_alias_collision"] += 1
                continue
            selected_prompt_tag = next(prompt for prompt, real in tag_map.items() if real == int(selected))
            key = (row.get("source_sequence_file", ""), int(action_obs.get("seq") or -1), str(ctx.get("ability_name")))
            if key in seen:
                continue
            seen.add(key)
            remaining_actions = _remaining_actions_after(row, action_index)
            pending_summary = str(ctx.get("pending_actions_summary") or "")
            if not pending_summary.strip():
                pending_summary = _pending_summary_from_actions(remaining_actions)
            conflict_hints = str(ctx.get("executor_conflict_hints") or "")
            if not conflict_hints.strip() and remaining_actions:
                conflict_hints = executor_conflict_hints_for_candidate_types(
                    [str(cand.get("type") or "") for cand in candidates],
                    remaining_actions,
                )
            system, user = _agent_pair(
                build_executor_messages(
                    ability_name=str(ctx.get("ability_name") or ""),
                    candidate_units_text=_candidate_units_text(candidates, tag_map),
                    cost_hint=str(ctx.get("cost_hint") or ""),
                    pending_actions_summary=pending_summary,
                    waiting_actions_summary=str(ctx.get("waiting_actions_summary") or ""),
                    executor_conflict_hints=conflict_hints,
                )
            )
            samples.append(sharegpt_sample("executor", mode, user, [selected_prompt_tag], system=system))
            qa["kept"] += 1
    return samples, qa


def build_all(
    labeled_steps: Path,
    output_dir: Path,
    shuffle_variants: int = 1,
    task: str = "all",
) -> dict[str, Any]:
    rows = list(read_jsonl(labeled_steps))
    output_dir.mkdir(parents=True, exist_ok=True)
    qa_report: dict[str, Any] = {}

    builders = {
        "naming": lambda mode: build_naming_samples(rows, mode),
        "ordering": lambda mode: build_ordering_samples(rows, mode, shuffle_variants),
        "executor": lambda mode: build_executor_samples(rows, mode),
    }
    mode_suffix = {"thinking": "thinking", "nothink": "nothink"}
    selected_tasks = list(builders) if task == "all" else [task]
    for task_name in selected_tasks:
        builder = builders[task_name]
        task_dir = output_dir / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        qa_report[task_name] = {}
        for mode in ("thinking", "nothink"):
            samples, qa = builder(mode)
            name = f"sc2_{task_name}_qwen3_{mode_suffix[mode]}_sft.json"
            write_json(task_dir / name, samples)
            qa_report[task_name][mode] = qa | {"file": str((task_dir / name).resolve())}

    if task == "all":
        write_json(output_dir / "dataset_info.fragment.json", dataset_info_fragment())
    write_json(output_dir / "qa_report.json", qa_report)
    return qa_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build naming, ordering, and executor ShareGPT SFT datasets.")
    parser.add_argument("--labeled-steps", required=True, help="Path to v8_steps/json/labeled_steps.jsonl.")
    parser.add_argument("--output", required=True, help="Output directory for SFT datasets.")
    parser.add_argument("--shuffle-variants", type=int, default=1, help="Ordering shuffled variants per step.")
    parser.add_argument("--task", choices=["all", "naming", "ordering", "executor"], default="all")
    args = parser.parse_args()

    report = build_all(Path(args.labeled_steps), Path(args.output), args.shuffle_variants, args.task)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
