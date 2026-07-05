"""Augment executor golden QA by remapping candidate tags within each sample."""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sft_pipeline.common.executor_golden_rank import (
    CANDIDATE_RE,
    classify_executor_rule_layer_from_prompt,
    parse_candidates,
    rank_executor_prompt,
)
from sft_pipeline.common.io import read_jsonl, write_json, write_jsonl

TagRemapStrategy = Literal["shuffle", "random"]
TAG_RE = re.compile(r"(?<=tag=)(\d+)\b")


def build_tag_remap(
    tags: list[int],
    *,
    rng: random.Random,
    strategy: TagRemapStrategy = "shuffle",
    tag_range: tuple[int, int] = (1, 999),
) -> dict[int, int]:
    if not tags:
        return {}
    if strategy == "shuffle":
        new_tags = list(tags)
        rng.shuffle(new_tags)
        return dict(zip(tags, new_tags))

    low, high = tag_range
    pool = [value for value in range(low, high + 1) if value not in tags]
    if len(pool) < len(tags):
        raise ValueError(f"not enough unused tags in range {low}-{high} for {len(tags)} candidates")
    new_tags = rng.sample(pool, k=len(tags))
    return dict(zip(tags, new_tags))


def non_identity_remap(
    tags: list[int],
    *,
    rng: random.Random,
    strategy: TagRemapStrategy,
    max_attempts: int = 32,
) -> dict[int, int]:
    if len(tags) < 2:
        return {tag: tag for tag in tags}
    for _ in range(max_attempts):
        mapping = build_tag_remap(tags, rng=rng, strategy=strategy)
        if any(old != new for old, new in mapping.items()):
            return mapping
    mapping = build_tag_remap(tags, rng=rng, strategy=strategy)
    old_tags = list(tags)
    new_tags = [mapping[tag] for tag in old_tags]
    new_tags.reverse()
    return dict(zip(old_tags, new_tags))


def apply_tag_remap_to_user(user: str, mapping: dict[int, int]) -> str:
    if not mapping:
        return user

    def replace_tag(match: re.Match[str]) -> str:
        old = int(match.group(1))
        return str(mapping.get(old, old))

    return TAG_RE.sub(replace_tag, user)


def remap_golden_rank(golden_rank: dict[str, Any], mapping: dict[int, int]) -> dict[str, Any]:
    remapped = copy.deepcopy(golden_rank)
    remapped["golden_tags"] = [mapping.get(tag, tag) for tag in remapped.get("golden_tags", [])]
    rankings = remapped.get("rankings")
    if not isinstance(rankings, list):
        return remapped
    for row in rankings:
        if not isinstance(row, dict):
            continue
        old_tag = int(row.get("tag", 0))
        new_tag = mapping.get(old_tag, old_tag)
        row["tag"] = new_tag
        sort_key = row.get("sort_key")
        if isinstance(sort_key, list) and sort_key and sort_key[-1] == -old_tag:
            sort_key[-1] = -new_tag
    return remapped


def augment_executor_record(
    record: dict[str, Any],
    *,
    rng: random.Random,
    strategy: TagRemapStrategy = "shuffle",
) -> dict[str, Any]:
    user = str(record.get("user") or "")
    candidates = parse_candidates(user)
    old_tags = [candidate.tag for candidate in candidates]
    mapping = non_identity_remap(old_tags, rng=rng, strategy=strategy)

    augmented = copy.deepcopy(record)
    augmented["user"] = apply_tag_remap_to_user(user, mapping)
    augmented["golden_tags"] = [mapping.get(tag, tag) for tag in record.get("golden_tags", [])]

    golden_rank = record.get("golden_rank")
    if isinstance(golden_rank, dict):
        augmented["golden_rank"] = remap_golden_rank(golden_rank, mapping)

    record_id = str(record.get("record_id") or "")
    if record_id:
        augmented["record_id"] = f"{record_id}__tagaug"

    return augmented


def validate_augmented_record(original: dict[str, Any], augmented: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    system = str(augmented.get("system") or "")
    user = str(augmented.get("user") or "")

    recomputed = rank_executor_prompt(system, user)
    expected = {int(tag) for tag in augmented.get("golden_tags", [])}
    if set(recomputed.golden_tags) != expected:
        errors.append(
            f"golden_tags mismatch: expected {sorted(expected)}, got {sorted(recomputed.golden_tags)}"
        )

    orig_layer = classify_executor_rule_layer_from_prompt(
        str(original.get("system") or ""),
        str(original.get("user") or ""),
    )[1]
    aug_layer = classify_executor_rule_layer_from_prompt(system, user)[1]
    if orig_layer != aug_layer:
        errors.append(f"rule layer changed: {orig_layer} -> {aug_layer}")

    candidates = parse_candidates(user)
    candidate_tags = [candidate.tag for candidate in candidates]
    if len(candidate_tags) != len(set(candidate_tags)):
        errors.append("duplicate candidate tags after remap")

    golden_tags = augmented.get("golden_tags", [])
    if not set(int(tag) for tag in golden_tags).issubset(set(candidate_tags)):
        errors.append("golden_tags not subset of candidate tags")

    return errors


def augment_executor_golden_records(
    records: list[dict[str, Any]],
    *,
    seed: int = 42,
    strategy: TagRemapStrategy = "shuffle",
    shuffle_output: bool = True,
    validate: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(seed)
    augmented_records: list[dict[str, Any]] = []
    validation_errors: list[dict[str, str]] = []

    for record in records:
        aug = augment_executor_record(record, rng=rng, strategy=strategy)
        if validate:
            errors = validate_augmented_record(record, aug)
            if errors:
                validation_errors.append(
                    {
                        "record_id": str(record.get("record_id") or ""),
                        "errors": "; ".join(errors),
                    }
                )
        augmented_records.append(aug)

    merged = list(records) + augmented_records
    if shuffle_output:
        rng.shuffle(merged)

    by_layer: Counter[str] = Counter()
    for record in merged:
        _, layer = classify_executor_rule_layer_from_prompt(
            str(record.get("system") or ""),
            str(record.get("user") or ""),
        )
        by_layer[layer] += 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_total": len(records),
        "augmented_count": len(augmented_records),
        "output_total": len(merged),
        "config": {
            "seed": seed,
            "strategy": strategy,
            "shuffle_output": shuffle_output,
            "validate": validate,
        },
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors[:20],
        "by_layer": dict(sorted(by_layer.items())),
    }
    return merged, report


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    default_input = (
        repo_root
        / "sft_pipeline_outputs/executor_golden_rank/SC2_executor_RL/executor_qa_golden_resampled.jsonl"
    )

    parser = argparse.ArgumentParser(description="Augment executor golden QA via within-sample tag remapping.")
    parser.add_argument("--input", type=Path, default=default_input, help="Input JSONL path")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path (default: <input_stem>_2x.jsonl beside input)",
    )
    parser.add_argument("--report", type=Path, default=None, help="Optional augment report JSON path")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--strategy",
        choices=("shuffle", "random"),
        default="shuffle",
        help="Tag remap strategy: shuffle existing tags or sample random unused short tags",
    )
    parser.add_argument(
        "--no-shuffle-output",
        action="store_true",
        help="Keep originals first, then augmented copies (no final shuffle)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip golden-rank recompute validation",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_path = (args.output or input_path.with_name(f"{input_path.stem}_2x{input_path.suffix}")).resolve()
    report_path = args.report or output_path.with_name("augment_report.json")

    records = list(read_jsonl(input_path))
    merged, report = augment_executor_golden_records(
        records,
        seed=args.seed,
        strategy=args.strategy,
        shuffle_output=not args.no_shuffle_output,
        validate=not args.no_validate,
    )
    if report["validation_error_count"]:
        raise SystemExit(
            f"validation failed for {report['validation_error_count']} augmented records; "
            f"see {report_path}"
        )

    report["input"] = str(input_path)
    report["output"] = str(output_path)

    write_jsonl(output_path, merged)
    write_json(report_path, report)

    print(json.dumps(report["by_layer"], ensure_ascii=False, indent=2))
    print(f"wrote {len(merged)} samples -> {output_path}")
    print(f"report -> {report_path}")


if __name__ == "__main__":
    main()
