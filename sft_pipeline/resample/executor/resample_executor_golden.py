"""Resample executor golden-rank data with balanced L0–L4 layers and per-layer actions."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.build_executor_golden_rank import slim_record
from sft_pipeline.common.executor_golden_rank import (
    RuleLayer,
    classify_executor_rule_layer_from_prompt,
)
from sft_pipeline.common.io import read_json, read_jsonl, write_json, write_jsonl

LAYERS: tuple[RuleLayer, ...] = ("L0", "L1", "L2", "L3", "L4")


def load_records(input_path: Path) -> list[dict[str, Any]]:
    if input_path.suffix.lower() == ".jsonl":
        return list(read_jsonl(input_path))
    payload = read_json(input_path)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    records: list[dict[str, Any]] = []
    by_strategy = payload.get("by_strategy")
    if isinstance(by_strategy, dict):
        for rows in by_strategy.values():
            if isinstance(rows, list):
                records.extend(row for row in rows if isinstance(row, dict))
    else:
        raw = payload.get("records")
        if isinstance(raw, list):
            records.extend(row for row in raw if isinstance(row, dict))
    return records


def bucket_records(records: list[dict[str, Any]]) -> tuple[dict[tuple[RuleLayer, str], list[int]], Counter]:
    buckets: dict[tuple[RuleLayer, str], list[int]] = defaultdict(list)
    layer_action_counts: Counter = Counter()
    for index, record in enumerate(records):
        system = str(record.get("system") or "")
        user = str(record.get("user") or "")
        ability, layer = classify_executor_rule_layer_from_prompt(system, user)
        buckets[(layer, ability)].append(index)
        layer_action_counts[(layer, ability)] += 1
    return dict(buckets), layer_action_counts


def equal_layer_action_targets(layer: RuleLayer, abilities: list[str], layer_target: int) -> dict[str, int]:
    if not abilities:
        return {}
    base = layer_target // len(abilities)
    remainder = layer_target % len(abilities)
    return {ability: base + (1 if idx < remainder else 0) for idx, ability in enumerate(abilities)}


def max_draws(pool_size: int, max_oversample_ratio: int) -> int:
    return pool_size * max(1, max_oversample_ratio)


def apply_oversample_cap(
    *,
    layer: RuleLayer,
    requested: dict[str, int],
    pool_sizes: dict[str, int],
    max_oversample_ratio: int,
) -> tuple[dict[str, int], list[dict[str, Any]], int]:
    """Cap scarce cells and redistribute deficit within the layer by remaining headroom."""
    effective = {
        ability: min(requested[ability], max_draws(pool_sizes[ability], max_oversample_ratio))
        for ability in requested
    }
    capped_events: list[dict[str, Any]] = []
    for ability, req in requested.items():
        got = effective[ability]
        if got < req:
            capped_events.append(
                {
                    "layer": layer,
                    "ability": ability,
                    "supply": pool_sizes[ability],
                    "requested": req,
                    "capped_to": got,
                    "max_oversample_ratio": max_oversample_ratio,
                }
            )

    deficit = sum(requested.values()) - sum(effective.values())
    if deficit <= 0:
        return effective, capped_events, 0

    headroom = {
        ability: max(0, max_draws(pool_sizes[ability], max_oversample_ratio) - effective[ability])
        for ability in requested
    }
    remaining = deficit
    redistribution: Counter[str] = Counter()
    while remaining > 0:
        candidates = [ability for ability, room in headroom.items() if room > 0]
        if not candidates:
            break
        total_headroom = sum(headroom[ability] for ability in candidates)
        allocated = 0
        for ability in candidates:
            if remaining <= 0:
                break
            share = max(1, round(remaining * headroom[ability] / total_headroom)) if total_headroom else 1
            add = min(share, headroom[ability], remaining)
            effective[ability] += add
            headroom[ability] -= add
            redistribution[ability] += add
            remaining -= add
            allocated += add
        if allocated == 0:
            break

    for ability, extra in redistribution.items():
        capped_events.append(
            {
                "layer": layer,
                "ability": ability,
                "redistributed_in": extra,
                "final_target": effective[ability],
            }
        )
    return effective, capped_events, remaining


def sample_indices(
    pool: list[int],
    target: int,
    *,
    rng: random.Random,
    max_oversample_ratio: int,
    records: list[dict[str, Any]],
    used_prompts: set[tuple[str, str]],
    prefer_unique_prompts: bool,
) -> tuple[list[int], dict[str, Any]]:
    if target <= 0:
        return [], {"target": 0, "selected": 0, "unique_records": 0, "duplicate_draws": 0}

    if not pool:
        return [], {"target": target, "selected": 0, "unique_records": 0, "duplicate_draws": 0, "error": "empty_pool"}

    ordered = list(pool)
    if prefer_unique_prompts:
        ordered.sort(
            key=lambda idx: (
                (str(records[idx].get("system") or ""), str(records[idx].get("user") or "")) in used_prompts,
                rng.random(),
            ),
        )
    else:
        rng.shuffle(ordered)

    selected: list[int] = []
    counts: Counter[int] = Counter()
    duplicate_draws = 0

    for idx in ordered:
        if len(selected) >= target:
            break
        selected.append(idx)
        counts[idx] += 1
        if prefer_unique_prompts:
            used_prompts.add((str(records[idx].get("system") or ""), str(records[idx].get("user") or "")))

    while len(selected) < target:
        available = [idx for idx in pool if counts[idx] < max_oversample_ratio]
        if not available:
            break
        idx = rng.choice(available)
        selected.append(idx)
        counts[idx] += 1
        duplicate_draws += 1

    return selected, {
        "target": target,
        "selected": len(selected),
        "unique_records": len(counts),
        "duplicate_draws": duplicate_draws,
        "max_per_record": max(counts.values()) if counts else 0,
        "shortfall": max(0, target - len(selected)),
    }


def resample_executor_golden(
    records: list[dict[str, Any]],
    *,
    layer_target: int = 200,
    seed: int = 42,
    max_oversample_ratio: int = 10,
    prefer_unique_prompts: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    buckets, input_layer_action = bucket_records(records)
    used_prompts: set[tuple[str, str]] = set()

    layer_abilities: dict[RuleLayer, list[str]] = {
        layer: sorted({ability for (layer_key, ability) in buckets if layer_key == layer}) for layer in LAYERS
    }

    final_targets: dict[tuple[RuleLayer, str], int] = {}
    cap_events: list[dict[str, Any]] = []
    layer_shortfall: dict[str, int] = {}

    for layer in LAYERS:
        abilities = layer_abilities[layer]
        if not abilities:
            layer_shortfall[layer] = layer_target
            continue
        requested = equal_layer_action_targets(layer, abilities, layer_target)
        pool_sizes = {ability: len(buckets[(layer, ability)]) for ability in abilities}
        effective, events, remaining = apply_oversample_cap(
            layer=layer,
            requested=requested,
            pool_sizes=pool_sizes,
            max_oversample_ratio=max_oversample_ratio,
        )
        cap_events.extend(events)
        layer_shortfall[layer] = remaining
        for ability, target in effective.items():
            final_targets[(layer, ability)] = target

    selected_indices: list[int] = []
    draw_stats: dict[str, dict[str, Any]] = {}
    for (layer, ability), target in sorted(final_targets.items()):
        pool = buckets[(layer, ability)]
        picks, stats = sample_indices(
            pool,
            target,
            rng=rng,
            max_oversample_ratio=max_oversample_ratio,
            records=records,
            used_prompts=used_prompts,
            prefer_unique_prompts=prefer_unique_prompts,
        )
        selected_indices.extend(picks)
        draw_stats[f"{layer}:{ability}"] = stats

    rng.shuffle(selected_indices)
    kept = [records[idx] for idx in selected_indices]

    by_layer: Counter[str] = Counter()
    by_layer_action: dict[str, Counter[str]] = {layer: Counter() for layer in LAYERS}
    for record in kept:
        ability, layer = classify_executor_rule_layer_from_prompt(
            str(record.get("system") or ""),
            str(record.get("user") or ""),
        )
        by_layer[layer] += 1
        by_layer_action[layer][ability] += 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_total": len(records),
        "output_total": len(kept),
        "config": {
            "layer_target": layer_target,
            "expected_total": layer_target * len(LAYERS),
            "seed": seed,
            "max_oversample_ratio": max_oversample_ratio,
            "prefer_unique_prompts": prefer_unique_prompts,
        },
        "input_by_layer_action": {
            f"{layer}:{ability}": int(count)
            for (layer, ability), count in sorted(input_layer_action.items())
        },
        "targets_by_layer_action": {
            f"{layer}:{ability}": target for (layer, ability), target in sorted(final_targets.items())
        },
        "by_layer": dict(sorted(by_layer.items())),
        "by_layer_action": {layer: dict(sorted(counter.items())) for layer, counter in by_layer_action.items()},
        "layer_shortfall_after_redistribution": dict(layer_shortfall),
        "cap_and_redistribution_events": cap_events,
        "draw_stats": draw_stats,
    }
    return kept, report


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    default_input = (
        repo_root
        / "sft_pipeline_outputs/executor_golden_rank/qwen17b_grpo_naming_27b_exec_10strat_macro_r5/executor_qa_golden.jsonl"
    )
    default_output_dir = default_input.parent / "resampled"

    parser = argparse.ArgumentParser(description="Resample executor golden QA with balanced L0–L4 layers.")
    parser.add_argument("--input", type=Path, default=default_input, help="executor_qa_golden.jsonl path")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output_dir,
        help="Directory for resampled JSON/JSONL and reports",
    )
    parser.add_argument("--layer-target", type=int, default=200, help="Target samples per rule layer (default 200 -> 1000 total)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-oversample-ratio", type=int, default=10)
    parser.add_argument(
        "--allow-duplicate-prompts",
        action="store_true",
        help="Do not prefer unseen (system, user) prompts when sampling without replacement.",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_records(input_path)
    kept, report = resample_executor_golden(
        records,
        layer_target=args.layer_target,
        seed=args.seed,
        max_oversample_ratio=args.max_oversample_ratio,
        prefer_unique_prompts=not args.allow_duplicate_prompts,
    )

    report["input"] = str(input_path)
    report["output_dir"] = str(output_dir)

    full_jsonl = output_dir / "executor_qa_golden_resampled.jsonl"
    slim_jsonl = output_dir / "executor_qa_golden_resampled_slim.jsonl"
    full_json = output_dir / "executor_qa_golden_resampled.json"
    slim_json = output_dir / "executor_qa_golden_resampled_slim.json"
    report_path = output_dir / "resample_report.json"
    config_path = output_dir / "resample_config.json"

    write_jsonl(full_jsonl, kept)
    write_jsonl(slim_jsonl, [slim_record(row) for row in kept])
    write_json(full_json, kept)
    write_json(slim_json, [slim_record(row) for row in kept])
    write_json(report_path, report)
    write_json(
        config_path,
        {
            "input": str(input_path),
            "output_dir": str(output_dir),
            **report["config"],
        },
    )

    print(json.dumps(report["by_layer"], ensure_ascii=False, indent=2))
    print(json.dumps(report["by_layer_action"], ensure_ascii=False, indent=2))
    print(f"wrote {len(kept)} samples -> {output_dir}")
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
