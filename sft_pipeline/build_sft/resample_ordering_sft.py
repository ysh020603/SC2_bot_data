"""Resample Ordering nocot SFT by ordered_actions multiset with step and action balancing."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.resample_naming_sft import (
    classify_tier,
    compute_cap,
    compute_step_targets,
    global_step_balanced_select,
    integer_step_targets,
    multiset_label,
    step_balance_stats,
)

STEP_RE = re.compile(r"\[Step\s+(\d+)\]", re.I)
SUMMARY_RE = re.compile(r"\[Strategy Summary\]\n(.*?)\n\nThe Strategy Summary", re.S)


def parse_gpt_answer(raw: str) -> list[str]:
    text = raw.strip()
    if "<think>" in text:
        idx = text.rfind("\n\n")
        text = text[idx + 2 :].strip() if idx >= 0 else text
    answer = json.loads(text)
    actions = answer.get("ordered_actions") or []
    if not isinstance(actions, list) or not actions:
        raise ValueError("ordered_actions must be a non-empty list")
    return [str(action) for action in actions]


def action_multiset_key(actions: list[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(actions).items()))


def parse_ordering_sample(index: int, sample: dict[str, Any]) -> dict[str, Any]:
    human = next(c["value"] for c in sample["conversations"] if c["from"] == "human")
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    step_match = STEP_RE.search(human)
    if not step_match:
        raise ValueError(f"sample {index}: missing [Step N]")
    summary_match = SUMMARY_RE.search(sample.get("system", ""))
    summary = summary_match.group(1).strip() if summary_match else f"__missing_summary__{index}"
    actions = parse_gpt_answer(gpt)
    return {
        "index": index,
        "step": int(step_match.group(1)),
        "summary": summary,
        "multiset": action_multiset_key(actions),
        "actions": actions,
        "action_set": set(actions),
        "sample": sample,
    }


def action_decision_points(records: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for record in records:
        for action in record["action_set"]:
            counts[action] += 1
    return counts


def multiset_supply(parsed: list[dict[str, Any]]) -> Counter[tuple[tuple[str, int], ...]]:
    return Counter(record["multiset"] for record in parsed)


def apply_action_floor(
    selected: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    *,
    floor_ratio: float,
    floor_max_dp: int,
    oversample_cap_ratio: float,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if floor_ratio <= 0 or floor_max_dp <= 0:
        return selected, []

    original_dp = action_decision_points(parsed)
    multiset_counts = multiset_supply(parsed)
    selected_indices = {record["index"] for record in selected}
    dropped = [record for record in parsed if record["index"] not in selected_indices]
    soft_max = max(1, math.ceil(len(selected) * oversample_cap_ratio))
    events: list[dict[str, Any]] = []
    blocked_actions: set[str] = set()

    while len(selected) < soft_max:
        current_dp = action_decision_points(selected)
        deficits: list[tuple[int, str, int]] = []
        for action, original in original_dp.items():
            if action in blocked_actions or original >= floor_max_dp:
                continue
            target = max(1, math.ceil(original * floor_ratio))
            gap = target - current_dp.get(action, 0)
            if gap > 0:
                deficits.append((gap, action, target))

        if not deficits:
            break

        deficits.sort(key=lambda item: (-item[0], item[1]))
        _, action, target = deficits[0]
        candidates = [
            record
            for record in dropped
            if record["index"] not in selected_indices and action in record["action_set"]
        ]
        if not candidates:
            events.append(
                {
                    "action": action,
                    "target": target,
                    "current": current_dp.get(action, 0),
                    "status": "no_candidate",
                }
            )
            blocked_actions.add(action)
            continue

        candidates.sort(
            key=lambda record: (
                1 if multiset_counts[record["multiset"]] == 1 else 0,
                record["step"],
                rng.random(),
            ),
            reverse=True,
        )
        record = candidates[0]
        enriched = dict(record)
        enriched["action_floor_boost"] = action
        selected.append(enriched)
        selected_indices.add(record["index"])
        events.append(
            {
                "action": action,
                "target": target,
                "current_before": current_dp.get(action, 0),
                "index": record["index"],
                "step": record["step"],
            }
        )

    return selected, events


def resample_ordering(
    samples: list[dict[str, Any]],
    *,
    target_size: int | None,
    seed: int,
    cfg: dict[str, float | int],
    step_balance_alpha: float,
    action_floor_ratio: float,
    action_floor_max_dp: int,
    oversample_cap_ratio: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rng = random.Random(seed)
    parsed = [parse_ordering_sample(i, sample) for i, sample in enumerate(samples)]

    buckets: dict[tuple[tuple[str, int], ...], list[dict[str, Any]]] = defaultdict(list)
    for record in parsed:
        buckets[record["multiset"]].append(record)

    planned_total = target_size if target_size is not None else len(parsed)
    float_targets = compute_step_targets(parsed, planned_total, step_balance_alpha)
    int_targets = integer_step_targets(float_targets, planned_total)

    selected, bucket_stats = global_step_balanced_select(
        buckets,
        cfg,
        int_targets,
        planned_total,
        rng,
    )
    selected, floor_events = apply_action_floor(
        selected,
        parsed,
        floor_ratio=action_floor_ratio,
        floor_max_dp=action_floor_max_dp,
        oversample_cap_ratio=oversample_cap_ratio,
        rng=rng,
    )

    kept_indices = sorted({record["index"] for record in selected})
    kept_samples = [samples[i] for i in kept_indices]
    manifest = build_manifest(parsed, kept_indices, bucket_stats, floor_events)
    report = build_report(
        samples,
        parsed,
        selected,
        bucket_stats,
        cfg,
        target_size,
        seed,
        step_balance_alpha,
        float_targets,
        int_targets,
        action_floor_ratio,
        action_floor_max_dp,
        floor_events,
    )
    return kept_samples, report, manifest


def build_manifest(
    parsed: list[dict[str, Any]],
    kept_indices: list[int],
    bucket_stats: list[dict[str, Any]],
    floor_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kept_set = set(kept_indices)
    cap_by_multiset = {
        stat["multiset"]: {"cap": stat["cap"], "original_count": stat["original_count"]}
        for stat in bucket_stats
    }
    boosted_indices = {event["index"] for event in floor_events if "index" in event}
    rows: list[dict[str, Any]] = []
    for record in parsed:
        label = multiset_label(record["multiset"])
        meta = cap_by_multiset.get(label, {})
        rows.append(
            {
                "index": record["index"],
                "kept": record["index"] in kept_set,
                "step": record["step"],
                "multiset": label,
                "bucket_original_count": meta.get("original_count"),
                "bucket_cap": meta.get("cap"),
                "action_floor_boost": record["index"] in boosted_indices,
                "n_actions": len(record["actions"]),
                "n_unique_actions": len(record["action_set"]),
            }
        )
    return rows


def build_report(
    original_samples: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    bucket_stats: list[dict[str, Any]],
    cfg: dict[str, float | int],
    target_size: int | None,
    seed: int,
    step_balance_alpha: float,
    float_step_targets: dict[int, float],
    int_step_targets: dict[int, int],
    action_floor_ratio: float,
    action_floor_max_dp: int,
    floor_events: list[dict[str, Any]],
) -> dict[str, Any]:
    def step_dist(records: list[dict[str, Any]]) -> dict[str, int]:
        counter = Counter(record["step"] for record in records)
        return {str(step): count for step, count in sorted(counter.items())}

    def multiset_dist(records: list[dict[str, Any]]) -> Counter[tuple[tuple[str, int], ...]]:
        return Counter(record["multiset"] for record in records)

    before_ms = multiset_dist(parsed)
    after_ms = multiset_dist(selected)
    before_action_dp = action_decision_points(parsed)
    after_action_dp = action_decision_points(selected)

    tier_drops = Counter()
    for stat in bucket_stats:
        tier = classify_tier(stat["original_count"], cfg)
        tier_drops[tier] += stat["original_count"] - stat["kept"]

    return {
        "seed": seed,
        "config": cfg,
        "step_balance_alpha": step_balance_alpha,
        "action_floor_ratio": action_floor_ratio,
        "action_floor_max_dp": action_floor_max_dp,
        "target_size_reference": target_size,
        "step_targets_float": {
            str(step): round(value, 2) for step, value in sorted(float_step_targets.items())
        },
        "step_targets_int": {str(step): value for step, value in sorted(int_step_targets.items())},
        "counts": {
            "original": len(original_samples),
            "kept": len({record["index"] for record in selected}),
            "dropped": len(original_samples) - len({record["index"] for record in selected}),
            "action_floor_boosted": sum(1 for event in floor_events if "index" in event),
        },
        "unique_multisets": {
            "original": len(before_ms),
            "kept": len(after_ms),
        },
        "step_distribution": {
            "before": step_dist(parsed),
            "after": step_dist(selected),
        },
        "step_balance": {
            "before": step_balance_stats(parsed),
            "after": step_balance_stats(selected),
        },
        "tier_dropped_samples": dict(sorted(tier_drops.items())),
        "top_multisets_before": [
            {
                "multiset": multiset_label(key),
                "count": count,
                "pct": round(100 * count / len(parsed), 2),
            }
            for key, count in before_ms.most_common(15)
        ],
        "top_multisets_after": [
            {
                "multiset": multiset_label(key),
                "count": count,
                "pct": round(100 * count / len(selected), 2),
            }
            for key, count in after_ms.most_common(15)
        ],
        "action_decision_points": {
            "before": dict(before_action_dp.most_common()),
            "after": dict(after_action_dp.most_common()),
        },
        "action_floor_events": floor_events,
        "step16_plus_pct": {
            "before": round(
                100 * sum(1 for record in parsed if record["step"] >= 16) / len(parsed),
                2,
            ),
            "after": round(
                100 * sum(1 for record in selected if record["step"] >= 16) / len(selected),
                2,
            ),
        },
        "n_total_actions_per_dp": {
            "before": {str(k): v for k, v in sorted(Counter(len(r["actions"]) for r in parsed).items())},
            "after": {str(k): v for k, v in sorted(Counter(len(r["actions"]) for r in selected).items())},
        },
        "n_unique_actions_per_dp": {
            "before": {str(k): v for k, v in sorted(Counter(len(r["action_set"]) for r in parsed).items())},
            "after": {str(k): v for k, v in sorted(Counter(len(r["action_set"]) for r in selected).items())},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Resample Ordering nocot SFT by answer multiset.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--target-size", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--t0-cap", type=int, default=22)
    parser.add_argument("--t1-cap", type=int, default=14)
    parser.add_argument("--t1-min-freq", type=int, default=25)
    parser.add_argument("--t2-ratio", type=float, default=0.55)
    parser.add_argument("--t3-ratio", type=float, default=0.85)
    parser.add_argument("--t4-ratio", type=float, default=1.0)
    parser.add_argument("--step-balance-alpha", type=float, default=0.65)
    parser.add_argument("--action-floor-ratio", type=float, default=0.75)
    parser.add_argument("--action-floor-max-dp", type=int, default=50)
    parser.add_argument("--oversample-cap-ratio", type=float, default=1.05)
    args = parser.parse_args()

    cfg = {
        "t0_min_freq": 50,
        "t0_cap": args.t0_cap,
        "t1_min_freq": args.t1_min_freq,
        "t1_cap": args.t1_cap,
        "t2_min_freq": 10,
        "t2_ratio": args.t2_ratio,
        "t3_min_freq": 5,
        "t3_ratio": args.t3_ratio,
        "t4_ratio": args.t4_ratio,
    }

    with args.input.open(encoding="utf-8") as f:
        samples = json.load(f)

    kept, report, manifest = resample_ordering(
        samples,
        target_size=args.target_size,
        seed=args.seed,
        cfg=cfg,
        step_balance_alpha=args.step_balance_alpha,
        action_floor_ratio=args.action_floor_ratio,
        action_floor_max_dp=args.action_floor_max_dp,
        oversample_cap_ratio=args.oversample_cap_ratio,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    manifest_path = args.manifest or args.output.with_name("ordering_resample_manifest.jsonl")
    with manifest_path.open("w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["step_balance"], ensure_ascii=False, indent=2))
    print(f"wrote {len(kept)} samples -> {args.output}")
    print(f"report -> {args.report}")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
