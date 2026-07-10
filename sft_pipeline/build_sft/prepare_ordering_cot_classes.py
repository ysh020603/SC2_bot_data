"""Tag Ordering resampled samples with tier + balance-class metadata for CoT rounds.

Two answer-structure tiers (based on gold ordered_actions):
  - Tier-1 "block": every action type occupies a contiguous run (no interleaving).
  - Tier-2 "interleaved": at least one action type is split by other actions.

Four balance classes (used to equalize kept counts during CoT annotation):
  - C1_block                    : Tier-1 (block ordered), any step.
  - C2_interleaved_early_rare   : Tier-2, step <= --early-step-max, rare multiset.
  - C3_interleaved_early_common : Tier-2, step <= --early-step-max, common multiset.
  - C4_interleaved_late         : Tier-2, step >  --early-step-max.

Emits a manifest JSONL keyed by sample index; the CoT round runner consumes it.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.inject_cot_sft import _ordered_actions, _parse_answer, _sample_parts
from sft_pipeline.common.io import read_json, write_json, write_jsonl

STEP_RE = re.compile(r"\[Step\s+(\d+)\]", re.I)


def parse_step(user: str) -> int:
    match = STEP_RE.search(user)
    return int(match.group(1)) if match else 0


def is_block_ordered(actions: list[str]) -> bool:
    positions: dict[str, list[int]] = defaultdict(list)
    for i, action in enumerate(actions):
        positions[action].append(i)
    return all(max(ps) - min(ps) + 1 == len(ps) for ps in positions.values())


def multiset_key(actions: list[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(actions).items()))


def classify(
    *,
    block: bool,
    step: int,
    multiset_freq: int,
    early_step_max: int,
    rare_max: int,
) -> str:
    if block:
        return "C1_block"
    if step <= early_step_max:
        if multiset_freq <= rare_max:
            return "C2_interleaved_early_rare"
        return "C3_interleaved_early_common"
    return "C4_interleaved_late"


def build_manifest(
    samples: list[dict[str, Any]],
    *,
    early_step_max: int,
    rare_max: int,
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    freq: Counter[tuple[tuple[str, int], ...]] = Counter()
    for index, sample in enumerate(samples):
        _, user, gold_text = _sample_parts(sample)
        actions = _ordered_actions(_parse_answer("ordering", gold_text))
        key = multiset_key(actions)
        freq[key] += 1
        parsed.append(
            {
                "index": index,
                "step": parse_step(user),
                "actions": actions,
                "multiset_key": key,
            }
        )

    manifest: list[dict[str, Any]] = []
    for row in parsed:
        actions = row["actions"]
        block = is_block_ordered(actions)
        balance_class = classify(
            block=block,
            step=row["step"],
            multiset_freq=freq[row["multiset_key"]],
            early_step_max=early_step_max,
            rare_max=rare_max,
        )
        manifest.append(
            {
                "index": row["index"],
                "step": row["step"],
                "tier": "block" if block else "interleaved",
                "balance_class": balance_class,
                "n_actions": len(actions),
                "n_unique_actions": len(set(actions)),
                "multiset_freq": freq[row["multiset_key"]],
            }
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Tag Ordering samples with tier + balance-class metadata.")
    parser.add_argument("--input", required=True, help="Ordering resampled SFT json (nothink or thinking).")
    parser.add_argument("--manifest", required=True, help="Output manifest JSONL path.")
    parser.add_argument("--report", default=None, help="Optional class-distribution report JSON path.")
    parser.add_argument("--early-step-max", type=int, default=10, help="Steps <= this count as early-game.")
    parser.add_argument("--rare-max", type=int, default=9, help="Multiset freq <= this counts as rare.")
    args = parser.parse_args()

    samples = read_json(Path(args.input))
    if not isinstance(samples, list):
        raise ValueError(f"{args.input} must contain a JSON list")

    manifest = build_manifest(samples, early_step_max=args.early_step_max, rare_max=args.rare_max)
    write_jsonl(Path(args.manifest), manifest)

    class_counts = Counter(row["balance_class"] for row in manifest)
    tier_counts = Counter(row["tier"] for row in manifest)
    report = {
        "input": str(Path(args.input).resolve()),
        "total": len(manifest),
        "early_step_max": args.early_step_max,
        "rare_max": args.rare_max,
        "tier_counts": dict(sorted(tier_counts.items())),
        "balance_class_counts": dict(sorted(class_counts.items())),
    }
    if args.report:
        write_json(Path(args.report), report)

    print(f"total: {report['total']}")
    print("tier_counts:")
    for key, value in report["tier_counts"].items():
        print(f"  {key}: {value}")
    print("balance_class_counts:")
    for key, value in report["balance_class_counts"].items():
        print(f"  {key}: {value}")
    print(f"manifest -> {Path(args.manifest).resolve()}")


if __name__ == "__main__":
    main()
