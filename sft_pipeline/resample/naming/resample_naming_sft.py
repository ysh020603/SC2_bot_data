"""Resample Naming SFT by answer NAME-SET class (ignore count and order).

- Class key = frozenset(items[].name): only which names appear, not how many, not order.
- Rare classes (original freq <= --rare-max) are kept in FULL.
- Common classes (freq > --rare-max) are capped at --per-class-cap (down-sample only).

Handles both thinking (`<think>...</think>\n\nJSON`) and nothink formats.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

STEP_RE = re.compile(r"\[Step\s+(\d+)\]", re.I)
SUMMARY_RE = re.compile(r"\[Strategy Summary\]\n(.*?)\n\nThe Strategy Summary", re.S)
THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_thinking(value: str) -> str:
    return THINK_RE.sub("", value, count=1).strip()


def class_key(items: list[dict[str, Any]]) -> frozenset[str]:
    return frozenset(str(item["name"]) for item in items)


def class_label(key: frozenset[str]) -> str:
    return "{" + ", ".join(sorted(key)) + "}"


def parse_sample(index: int, sample: dict[str, Any]) -> dict[str, Any]:
    human = next(c["value"] for c in sample["conversations"] if c["from"] == "human")
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    step_match = STEP_RE.search(human)
    step = int(step_match.group(1)) if step_match else -1
    summary_match = SUMMARY_RE.search(sample.get("system", ""))
    summary = summary_match.group(1).strip() if summary_match else f"__missing_summary__{index}"
    answer = json.loads(strip_thinking(gpt))
    items = answer.get("items") or []
    if not items:
        raise ValueError(f"sample {index}: empty items answer")
    return {
        "index": index,
        "step": step,
        "summary": summary,
        "class": class_key(items),
        "n_names": len(class_key(items)),
    }


def cap_select(records: list[dict[str, Any]], cap: int, rng: random.Random) -> list[dict[str, Any]]:
    """Pick `cap` records from a class, spreading across distinct steps for diversity."""
    if len(records) <= cap:
        return list(records)

    by_step: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_step[record["step"]].append(record)
    for step_records in by_step.values():
        rng.shuffle(step_records)

    steps = list(by_step)
    rng.shuffle(steps)

    selected: list[dict[str, Any]] = []
    while len(selected) < cap:
        progressed = False
        for step in steps:
            if by_step[step]:
                selected.append(by_step[step].pop())
                progressed = True
                if len(selected) >= cap:
                    break
        if not progressed:
            break
    return selected


def resample_by_class(
    samples: list[dict[str, Any]],
    *,
    rare_max: int,
    per_class_cap: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    parsed = [parse_sample(i, sample) for i, sample in enumerate(samples)]

    buckets: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    for record in parsed:
        buckets[record["class"]].append(record)

    kept_indices: list[int] = []
    per_class_stats: list[dict[str, Any]] = []
    rare_classes = rare_samples = 0
    common_classes = common_capped = common_samples = 0

    for key, records in buckets.items():
        freq = len(records)
        if freq <= rare_max:
            chosen = records
            tier = "rare"
            rare_classes += 1
            rare_samples += len(chosen)
        else:
            chosen = cap_select(records, per_class_cap, rng)
            tier = "common"
            common_classes += 1
            common_samples += len(chosen)
            if freq > per_class_cap:
                common_capped += 1
        kept_indices.extend(record["index"] for record in chosen)
        per_class_stats.append(
            {
                "class": class_label(key),
                "tier": tier,
                "original": freq,
                "kept": len(chosen),
            }
        )

    kept_indices.sort()
    kept_samples = [samples[i] for i in kept_indices]
    report = build_report(
        parsed,
        kept_indices,
        per_class_stats,
        buckets,
        rare_max=rare_max,
        per_class_cap=per_class_cap,
        seed=seed,
        rare_classes=rare_classes,
        rare_samples=rare_samples,
        common_classes=common_classes,
        common_capped=common_capped,
        common_samples=common_samples,
    )
    return kept_samples, report


def _step_dist(records: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(record["step"] for record in records)
    return {str(step): count for step, count in sorted(counter.items())}


def _size_hist(counts: list[int]) -> dict[str, int]:
    hist = Counter(counts)
    return {str(size): n for size, n in sorted(hist.items())}


def build_report(
    parsed: list[dict[str, Any]],
    kept_indices: list[int],
    per_class_stats: list[dict[str, Any]],
    buckets: dict[frozenset[str], list[dict[str, Any]]],
    *,
    rare_max: int,
    per_class_cap: int,
    seed: int,
    rare_classes: int,
    rare_samples: int,
    common_classes: int,
    common_capped: int,
    common_samples: int,
) -> dict[str, Any]:
    kept_set = set(kept_indices)
    kept_records = [record for record in parsed if record["index"] in kept_set]

    before_sizes = [len(records) for records in buckets.values()]
    after_counts: Counter[frozenset[str]] = Counter(record["class"] for record in kept_records)
    after_sizes = list(after_counts.values())

    top_before = sorted(per_class_stats, key=lambda row: row["original"], reverse=True)[:15]
    top_after = sorted(per_class_stats, key=lambda row: row["kept"], reverse=True)[:15]

    return {
        "rule": "class = frozenset(items[].name); rare classes kept in full; common capped",
        "config": {
            "rare_max": rare_max,
            "per_class_cap": per_class_cap,
            "seed": seed,
            "oversample": False,
        },
        "counts": {
            "original": len(parsed),
            "kept": len(kept_indices),
            "dropped": len(parsed) - len(kept_indices),
        },
        "classes": {
            "unique_total": len(buckets),
            "rare_classes": rare_classes,
            "rare_samples_kept": rare_samples,
            "common_classes": common_classes,
            "common_samples_kept": common_samples,
            "common_classes_capped": common_capped,
        },
        "class_size_histogram": {
            "before": _size_hist(before_sizes),
            "after": _size_hist(after_sizes),
        },
        "step_distribution": {
            "before": _step_dist(parsed),
            "after": _step_dist(kept_records),
        },
        "top_classes_before": top_before,
        "top_classes_after": top_after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resample Naming SFT by answer name-set class (ignore count and order)."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--rare-max",
        type=int,
        default=2,
        help="Classes with original freq <= this are packed as one bucket and kept in full.",
    )
    parser.add_argument(
        "--per-class-cap",
        type=int,
        default=8,
        help="Max samples kept per common class (down-sample only).",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as f:
        samples = json.load(f)

    kept, report = resample_by_class(
        samples,
        rare_max=args.rare_max,
        per_class_cap=args.per_class_cap,
        seed=args.seed,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
        f.write("\n")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["classes"], ensure_ascii=False, indent=2))
    print(f"wrote {len(kept)} samples -> {args.output}")
    print(f"report -> {args.report}")


if __name__ == "__main__":
    main()
