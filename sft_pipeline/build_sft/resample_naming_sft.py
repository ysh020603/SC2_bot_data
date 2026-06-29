"""Resample Naming nocot SFT by answer multiset with optional step balancing."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

STEP_RE = re.compile(r"\[Step\s+(\d+)\]", re.I)
SUMMARY_RE = re.compile(r"\[Strategy Summary\]\n(.*?)\n\nThe Strategy Summary", re.S)


def multiset_key(items: list[dict[str, Any]]) -> tuple[tuple[str, int], ...]:
    counts: Counter[str] = Counter()
    for item in items:
        counts[str(item["name"])] += int(item["count"])
    return tuple(sorted(counts.items()))


def multiset_label(key: tuple[tuple[str, int], ...]) -> str:
    parts: list[str] = []
    for name, count in key:
        parts.append(f"{name}x{count}" if count > 1 else name)
    return ", ".join(parts)


def parse_sample(index: int, sample: dict[str, Any]) -> dict[str, Any]:
    human = next(c["value"] for c in sample["conversations"] if c["from"] == "human")
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    step_match = STEP_RE.search(human)
    if not step_match:
        raise ValueError(f"sample {index}: missing [Step N]")
    summary_match = SUMMARY_RE.search(sample.get("system", ""))
    summary = summary_match.group(1).strip() if summary_match else f"__missing_summary__{index}"
    answer = json.loads(gpt.strip())
    items = answer.get("items") or []
    if not items:
        raise ValueError(f"sample {index}: empty items answer")
    return {
        "index": index,
        "step": int(step_match.group(1)),
        "summary": summary,
        "multiset": multiset_key(items),
        "sample": sample,
    }


def compute_cap(freq: int, cfg: dict[str, float | int]) -> int:
    if freq >= int(cfg["t0_min_freq"]):
        return min(freq, int(cfg["t0_cap"]))
    if freq >= int(cfg["t1_min_freq"]):
        return min(freq, int(cfg["t1_cap"]))
    if freq >= int(cfg["t2_min_freq"]):
        return max(1, math.ceil(freq * float(cfg["t2_ratio"])))
    if freq >= int(cfg["t3_min_freq"]):
        return max(1, math.ceil(freq * float(cfg["t3_ratio"])))
    if freq >= 2:
        return max(1, math.ceil(freq * float(cfg["t4_ratio"])))
    return freq


def compute_step_targets(
    parsed: list[dict[str, Any]],
    total: int,
    alpha: float,
    *,
    eligible_min_supply: int = 5,
) -> dict[int, float]:
    """Blend flat per-step targets with the original distribution, capped by supply."""
    if total <= 0 or not parsed:
        return {}

    alpha = max(0.0, min(1.0, alpha))
    orig = Counter(record["step"] for record in parsed)
    steps = sorted(orig)
    if alpha <= 0:
        return {step: float(orig[step]) for step in steps}

    eligible = [step for step in steps if orig[step] >= eligible_min_supply]
    flat = total / len(eligible) if eligible else total / len(steps)

    targets: dict[int, float] = {}
    for step in steps:
        proportional = orig[step] / len(parsed) * total
        if step in eligible:
            blended = alpha * flat + (1.0 - alpha) * proportional
        else:
            blended = proportional
        targets[step] = min(blended, float(orig[step]))

    target_sum = sum(targets.values())
    if target_sum > total:
        scale = total / target_sum
        for step in steps:
            if orig[step] >= 1:
                targets[step] = max(1.0, targets[step] * scale)

    return targets


def dedupe_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_summary: dict[str, dict[str, Any]] = {}
    for record in records:
        summary = record["summary"]
        if summary not in by_summary or record["step"] > by_summary[summary]["step"]:
            by_summary[summary] = record
    return list(by_summary.values())


def integer_step_targets(float_targets: dict[int, float], total: int) -> dict[int, int]:
    if not float_targets:
        return {}
    floors = {step: int(value) for step, value in float_targets.items()}
    assigned = sum(floors.values())
    remainders = sorted(
        float_targets.items(),
        key=lambda item: (item[1] - int(item[1]), item[0]),
        reverse=True,
    )
    targets = dict(floors)
    idx = 0
    while assigned < total and idx < len(remainders) * 3:
        step = remainders[idx % len(remainders)][0]
        targets[step] += 1
        assigned += 1
        idx += 1
    while assigned > total:
        step = min(
            targets,
            key=lambda s: (targets[s] - float_targets.get(s, 0.0), s),
        )
        if targets[step] <= 1:
            break
        targets[step] -= 1
        assigned -= 1
    return targets


def pick_one_per_multiset(
    buckets: dict[tuple[tuple[str, int], ...], list[dict[str, Any]]],
    step_targets: dict[int, int],
    rng: random.Random,
) -> tuple[list[dict[str, Any]], Counter[int], set[int]]:
    step_selected: Counter[int] = Counter()
    selected: list[dict[str, Any]] = []
    selected_indices: set[int] = set()
    multiset_items = list(buckets.items())
    rng.shuffle(multiset_items)

    for multiset, records in multiset_items:
        candidates = dedupe_candidates(records)
        if not candidates:
            continue
        candidates.sort(
            key=lambda record: (
                step_targets.get(record["step"], 0) - step_selected[record["step"]],
                rng.random(),
            ),
            reverse=True,
        )
        record = candidates[0]
        for candidate in candidates:
            step = candidate["step"]
            if step_selected[step] < step_targets.get(step, 0):
                record = candidate
                break
        selected.append(record)
        selected_indices.add(record["index"])
        step_selected[record["step"]] += 1

    return selected, step_selected, selected_indices


def global_step_balanced_select(
    buckets: dict[tuple[tuple[str, int], ...], list[dict[str, Any]]],
    cfg: dict[str, float | int],
    step_targets: dict[int, int],
    soft_max: int | None,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bucket_caps = {
        multiset: compute_cap(len(records), cfg) for multiset, records in buckets.items()
    }
    bucket_candidates: dict[tuple[tuple[str, int], ...], list[dict[str, Any]]] = {
        multiset: dedupe_candidates(records) for multiset, records in buckets.items()
    }
    bucket_selected: Counter[tuple[tuple[str, int], ...]] = Counter()

    base_selected, step_selected, selected_indices = pick_one_per_multiset(
        buckets,
        step_targets,
        rng,
    )
    for record in base_selected:
        bucket_selected[record["multiset"]] += 1

    selected: list[dict[str, Any]] = []
    for record in base_selected:
        enriched = dict(record)
        enriched["bucket_freq"] = len(buckets[record["multiset"]])
        enriched["bucket_cap"] = bucket_caps[record["multiset"]]
        selected.append(enriched)

    def available_candidates(step: int) -> list[tuple[tuple[tuple[str, int], ...], dict[str, Any]]]:
        items: list[tuple[tuple[tuple[str, int], ...], dict[str, Any]]] = []
        for multiset, candidates in bucket_candidates.items():
            if bucket_selected[multiset] >= bucket_caps[multiset]:
                continue
            for record in candidates:
                if record["step"] == step and record["index"] not in selected_indices:
                    items.append((multiset, record))
        return items

    steps = sorted(step_targets)
    max_rounds = (soft_max or sum(step_targets.values())) * 5 + 1000
    for _ in range(max_rounds):
        if soft_max is not None and len(selected) >= soft_max:
            break
        steps.sort(
            key=lambda step: (
                step_targets[step] - step_selected[step],
                rng.random(),
            ),
            reverse=True,
        )
        if steps and step_targets[steps[0]] - step_selected[steps[0]] <= 0:
            break
        added = False
        for step in steps:
            if step_selected[step] >= step_targets[step]:
                continue
            candidates = available_candidates(step)
            if not candidates:
                continue
            candidates.sort(
                key=lambda item: (
                    bucket_caps[item[0]] - bucket_selected[item[0]],
                    rng.random(),
                ),
                reverse=True,
            )
            multiset, record = candidates[0]
            enriched = dict(record)
            enriched["bucket_freq"] = len(buckets[multiset])
            enriched["bucket_cap"] = bucket_caps[multiset]
            selected.append(enriched)
            selected_indices.add(record["index"])
            bucket_selected[multiset] += 1
            step_selected[step] += 1
            added = True
            break
        if not added:
            break

    bucket_stats = [
        {
            "multiset": multiset_label(multiset),
            "original_count": len(buckets[multiset]),
            "cap": bucket_caps[multiset],
            "kept": bucket_selected[multiset],
        }
        for multiset in buckets
    ]
    return selected, bucket_stats


def step_balance_stats(records: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {"min": 0.0, "max": 0.0, "ratio": 0.0, "std": 0.0}
    counts = Counter(record["step"] for record in records)
    values = list(counts.values())
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    min_v = min(values)
    max_v = max(values)
    return {
        "min": float(min_v),
        "max": float(max_v),
        "ratio": float(max_v / min_v) if min_v else 0.0,
        "std": float(math.sqrt(variance)),
    }


def resample_naming(
    samples: list[dict[str, Any]],
    *,
    target_size: int | None,
    seed: int,
    cfg: dict[str, float | int],
    step_balance_alpha: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    parsed = [parse_sample(i, sample) for i, sample in enumerate(samples)]

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

    trim_dropped: list[dict[str, Any]] = []

    kept_indices = sorted(record["index"] for record in selected)
    kept_samples = [samples[i] for i in kept_indices]
    report = build_report(
        samples,
        parsed,
        selected,
        trim_dropped,
        bucket_stats,
        cfg,
        target_size,
        seed,
        step_balance_alpha,
        float_targets,
        int_targets,
    )
    return kept_samples, report


def build_report(
    original_samples: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    trim_dropped: list[dict[str, Any]],
    bucket_stats: list[dict[str, Any]],
    cfg: dict[str, float | int],
    target_size: int | None,
    seed: int,
    step_balance_alpha: float,
    float_step_targets: dict[int, float],
    int_step_targets: dict[int, int],
) -> dict[str, Any]:
    def step_dist(records: list[dict[str, Any]]) -> dict[str, int]:
        counter = Counter(record["step"] for record in records)
        return {str(step): count for step, count in sorted(counter.items())}

    def multiset_dist(records: list[dict[str, Any]]) -> Counter[tuple[tuple[str, int], ...]]:
        return Counter(record["multiset"] for record in records)

    before_ms = multiset_dist(parsed)
    after_ms = multiset_dist(selected)
    top_before = [
        {"multiset": multiset_label(key), "count": count, "pct": round(100 * count / len(parsed), 2)}
        for key, count in before_ms.most_common(15)
    ]
    top_after = [
        {
            "multiset": multiset_label(key),
            "count": count,
            "pct": round(100 * count / len(selected), 2),
        }
        for key, count in after_ms.most_common(15)
    ]

    tier_drops = Counter()
    for stat in bucket_stats:
        tier = classify_tier(stat["original_count"], cfg)
        tier_drops[tier] += stat["original_count"] - stat["kept"]

    return {
        "seed": seed,
        "config": cfg,
        "step_balance_alpha": step_balance_alpha,
        "target_size_reference": target_size,
        "step_targets_float": {
            str(step): round(value, 2) for step, value in sorted(float_step_targets.items())
        },
        "step_targets_int": {str(step): value for step, value in sorted(int_step_targets.items())},
        "counts": {
            "original": len(original_samples),
            "kept": len(selected),
            "dropped": len(original_samples) - len(selected),
            "trim_dropped": len(trim_dropped),
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
        "top_multisets_before": top_before,
        "top_multisets_after": top_after,
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
    }


def classify_tier(freq: int, cfg: dict[str, float | int]) -> str:
    if freq >= int(cfg["t0_min_freq"]):
        return "T0"
    if freq >= int(cfg["t1_min_freq"]):
        return "T1"
    if freq >= int(cfg["t2_min_freq"]):
        return "T2"
    if freq >= int(cfg["t3_min_freq"]):
        return "T3"
    if freq >= 2:
        return "T4"
    return "T5_singleton"


def main() -> None:
    parser = argparse.ArgumentParser(description="Resample Naming nocot SFT by answer multiset.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--target-size",
        type=int,
        default=3000,
        help="Reference budget for step-target planning and soft upper cap; actual size may differ.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--t0-cap", type=int, default=25)
    parser.add_argument("--t1-cap", type=int, default=15)
    parser.add_argument("--t2-ratio", type=float, default=0.5)
    parser.add_argument("--t3-ratio", type=float, default=0.75)
    parser.add_argument("--t4-ratio", type=float, default=1.0)
    parser.add_argument(
        "--step-balance-alpha",
        type=float,
        default=0.65,
        help="0=keep original step proportions, 1=uniform step targets (blended, supply-capped).",
    )
    args = parser.parse_args()

    cfg = {
        "t0_min_freq": 50,
        "t0_cap": args.t0_cap,
        "t1_min_freq": 30,
        "t1_cap": args.t1_cap,
        "t2_min_freq": 10,
        "t2_ratio": args.t2_ratio,
        "t3_min_freq": 5,
        "t3_ratio": args.t3_ratio,
        "t4_ratio": args.t4_ratio,
    }

    with args.input.open(encoding="utf-8") as f:
        samples = json.load(f)

    kept, report = resample_naming(
        samples,
        target_size=args.target_size,
        seed=args.seed,
        cfg=cfg,
        step_balance_alpha=args.step_balance_alpha,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
        f.write("\n")

    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["step_balance"], ensure_ascii=False, indent=2))
    print(f"wrote {len(kept)} samples -> {args.output}")
    print(f"report -> {args.report}")


if __name__ == "__main__":
    main()
