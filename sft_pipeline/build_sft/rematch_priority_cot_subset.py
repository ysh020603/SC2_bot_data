"""Rematch priority CoT audit/recover rows onto a new input subset by gold answer."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.inject_cot_sft import (
    _parse_answer,
    _rebuild_kept_samples_from_audit,
    _sample_parts,
    rule_check_naming,
)
from sft_pipeline.common.io import read_json, read_jsonl, reset_jsonl, write_json


def _gold_key(answer: Any) -> str:
    return json.dumps(answer, ensure_ascii=False, sort_keys=True)


def _build_gold_index(samples: list[dict[str, Any]]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for i, sample in enumerate(samples):
        _, _, gold_text = _sample_parts(sample)
        gold = _parse_answer("naming", gold_text)
        index[_gold_key(gold)].append(i)
    return index


def _allocate_index(gold_key: str, gold_index: dict[str, list[int]], used: set[int]) -> int | None:
    for idx in gold_index.get(gold_key, []):
        if idx not in used:
            used.add(idx)
            return idx
    return None


def rematch_priority(
    *,
    samples: list[dict[str, Any]],
    source_audit: Path | None,
    source_rejected_detail: Path | None,
    audit_file: Path,
    output_sft: Path,
) -> dict[str, Any]:
    gold_index = _build_gold_index(samples)
    used: set[int] = set()
    rematched_audit: list[dict[str, Any]] = []

    def _try_add(row: dict[str, Any], *, from_audit: bool) -> bool:
        gold = row.get("gold_answer")
        generated = row.get("generated_answer")
        cot = row.get("generated_cot")
        if gold is None or generated is None or not cot:
            return False
        rule = rule_check_naming(gold, generated, str(cot))
        if not rule.passed:
            return False
        idx = _allocate_index(_gold_key(gold), gold_index, used)
        if idx is None:
            return False
        entry = {
            "index": idx,
            "task": "naming",
            "decision": row.get("decision") or "recovered_rule_pass_use_generated",
            "gold_answer": gold,
            "generated_cot": cot,
            "generated_answer": generated,
            "final_answer": row.get("final_answer") or generated,
            "rule": {"passed": True, "reasons": [], "metrics": rule.metrics},
            "teacher": row.get("teacher"),
            "generation": row.get("generation"),
            "teacher_model": row.get("teacher_model"),
            "recovered_from_reject": row.get("recovered_from_reject", not from_audit),
            "rematched_to_subset": True,
        }
        if row.get("original_reject_reason"):
            entry["original_reject_reason"] = row["original_reject_reason"]
        rematched_audit.append(entry)
        return True

    from_audit = 0
    if source_audit and source_audit.exists():
        for row in read_jsonl(source_audit):
            if _try_add(row, from_audit=True):
                from_audit += 1

    from_reject = 0
    seen_gold_cot: set[tuple[str, str]] = set()
    if source_rejected_detail and source_rejected_detail.exists():
        for row in read_jsonl(source_rejected_detail):
            if row.get("stage") != "rule_check":
                continue
            key = (_gold_key(row.get("gold_answer")), str(row.get("generated_cot") or "")[:200])
            if key in seen_gold_cot:
                continue
            if _try_add(row, from_audit=False):
                from_reject += 1
                seen_gold_cot.add(key)

    rematched_audit.sort(key=lambda row: int(row["index"]))
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    reset_jsonl(audit_file)
    with audit_file.open("w", encoding="utf-8") as handle:
        for row in rematched_audit:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    kept = _rebuild_kept_samples_from_audit(audit_file, samples, "naming")
    write_json(output_sft, kept)
    return {
        "subset_samples": len(samples),
        "rematched_audit": len(rematched_audit),
        "from_prior_audit": from_audit,
        "from_prior_reject": from_reject,
        "output_kept": len(kept),
        "used_indices": len(used),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rematch priority CoT rows onto new subset indices.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, default=None)
    parser.add_argument("--source-rejected-detail", type=Path, default=None)
    parser.add_argument("--audit-file", type=Path, required=True)
    parser.add_argument("--output-sft", type=Path, required=True)
    args = parser.parse_args()

    samples = read_json(args.input)
    if not isinstance(samples, list):
        raise ValueError("input must be a JSON array")
    report = rematch_priority(
        samples=samples,
        source_audit=args.source_audit,
        source_rejected_detail=args.source_rejected_detail,
        audit_file=args.audit_file,
        output_sft=args.output_sft,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
