"""Re-evaluate naming rule_check rejects under current rules and recover kept CoT samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.inject_cot_sft import (
    _rebuild_kept_samples_from_audit,
    rule_check_naming,
)
from sft_pipeline.common.io import append_jsonl, read_json, read_jsonl, write_json


def _audit_indices(audit_file: Path) -> set[int]:
    if not audit_file.exists():
        return set()
    return {int(row["index"]) for row in read_jsonl(audit_file) if row.get("index") is not None}


def recover_from_rejects(
    *,
    samples: list[dict[str, Any]],
    rejected_detail: Path,
    audit_file: Path,
    output_sft: Path,
    decision: str = "recovered_rule_pass_use_generated",
) -> dict[str, Any]:
    existing = _audit_indices(audit_file)
    recovered = 0
    still_fail = 0
    skipped = 0
    fail_reasons: dict[str, int] = {}

    audit_file.parent.mkdir(parents=True, exist_ok=True)
    for row in read_jsonl(rejected_detail):
        if row.get("stage") != "rule_check":
            continue
        index = int(row["index"])
        if index in existing:
            skipped += 1
            continue
        generated = row.get("generated_answer")
        cot = row.get("generated_cot")
        gold = row.get("gold_answer")
        if generated is None or not cot or gold is None:
            still_fail += 1
            continue
        rule = rule_check_naming(gold, generated, str(cot))
        if not rule.passed:
            still_fail += 1
            for reason in rule.reasons:
                fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
            continue
        if index < 0 or index >= len(samples):
            still_fail += 1
            continue
        final_answer = generated
        audit = {
            "index": index,
            "task": "naming",
            "decision": decision,
            "gold_answer": gold,
            "generated_cot": cot,
            "generated_answer": generated,
            "final_answer": final_answer,
            "rule": {"passed": rule.passed, "reasons": rule.reasons, "metrics": rule.metrics},
            "teacher": None,
            "generation": row.get("generation"),
            "teacher_model": None,
            "recovered_from_reject": True,
            "original_reject_reason": row.get("reason"),
        }
        append_jsonl(audit_file, audit)
        existing.add(index)
        recovered += 1

    kept = _rebuild_kept_samples_from_audit(audit_file, samples, "naming")
    write_json(output_sft, kept)
    return {
        "recovered": recovered,
        "still_fail": still_fail,
        "skipped_existing_audit": skipped,
        "audit_total": len(existing),
        "output_kept": len(kept),
        "remaining_fail_reasons_top": sorted(fail_reasons.items(), key=lambda x: -x[1])[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover naming CoT samples from rule_check rejects.")
    parser.add_argument("--input", type=Path, required=True, help="Source thinking ShareGPT JSON array.")
    parser.add_argument("--rejected-detail", type=Path, required=True)
    parser.add_argument("--audit-file", type=Path, required=True)
    parser.add_argument("--output-sft", type=Path, required=True)
    args = parser.parse_args()

    samples = read_json(args.input)
    if not isinstance(samples, list):
        raise ValueError(f"{args.input} must be a JSON array")

    report = recover_from_rejects(
        samples=samples,
        rejected_detail=args.rejected_detail,
        audit_file=args.audit_file,
        output_sft=args.output_sft,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
