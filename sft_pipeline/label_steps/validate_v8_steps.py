from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import iter_sequence_files, read_json, read_jsonl, write_json
from sft_pipeline.label_steps.build_v8_steps import _expected_md_path, _sample_key
from sft_pipeline.label_steps.sequence_order import LLM_FAILED_MARK, bot_folder_for_sequence, md_is_valid


def validate_v8_output(
    data_dir: Path,
    output_dir: Path,
    *,
    require_victory: bool = True,
) -> dict[str, Any]:
    md_dir = output_dir / "md"
    json_dir = output_dir / "json"
    labeled_path = json_dir / "labeled_steps.jsonl"

    totals = {
        "sequence_files": 0,
        "victory_sequences": 0,
        "expected_md_files": 0,
        "existing_md_files": 0,
        "valid_md_files": 0,
        "invalid_md_files": 0,
        "missing_md_files": 0,
        "labeled_rows": 0,
        "empty_step_text": 0,
        "failed_step_text": 0,
        "missing_obs_text": 0,
    }
    invalid_md: list[str] = []
    missing_md: list[str] = []
    row_issues: list[dict[str, Any]] = []

    victory_keys: set[str] = set()
    for seq_path in iter_sequence_files(data_dir):
        totals["sequence_files"] += 1
        seq_data = read_json(seq_path)
        meta = seq_data.get("meta", {})
        if require_victory and meta.get("result") != "Victory":
            continue
        order_list = seq_data.get("order_list") or []
        sequence = seq_data.get("sequence") or []
        if not order_list or not sequence:
            continue

        totals["victory_sequences"] += 1
        bot_folder = bot_folder_for_sequence(data_dir, seq_path)
        sample_key = _sample_key(bot_folder, seq_path, meta)
        victory_keys.add(sample_key)
        totals["expected_md_files"] += 1

        md_path = _expected_md_path(md_dir, data_dir, seq_path)
        if not md_path.exists():
            totals["missing_md_files"] += 1
            missing_md.append(sample_key)
            continue

        totals["existing_md_files"] += 1
        if md_is_valid(md_path):
            totals["valid_md_files"] += 1
        else:
            totals["invalid_md_files"] += 1
            invalid_md.append(sample_key)

    if labeled_path.exists():
        for row in read_jsonl(labeled_path):
            totals["labeled_rows"] += 1
            step_text = str(row.get("step_text_v8") or "").strip()
            issues: list[str] = []
            if not step_text:
                totals["empty_step_text"] += 1
                issues.append("empty_step_text_v8")
            if LLM_FAILED_MARK in step_text:
                totals["failed_step_text"] += 1
                issues.append("llm_call_failed_mark")
            obs = row.get("obs_at_step_start") or {}
            if not obs.get("text"):
                totals["missing_obs_text"] += 1
                issues.append("missing_obs_text")
            if issues:
                row_issues.append({"sample_id": row.get("sample_id"), "issues": issues})

    passed = (
        totals["invalid_md_files"] == 0
        and totals["missing_md_files"] == 0
        and totals["empty_step_text"] == 0
        and totals["failed_step_text"] == 0
        and totals["labeled_rows"] > 0
        and totals["victory_sequences"] == totals["valid_md_files"]
    )

    return {
        "passed": passed,
        "totals": totals,
        "invalid_md_samples": invalid_md,
        "missing_md_samples": missing_md,
        "row_issues": row_issues[:50],
        "data_dir": str(data_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "victory_sample_count": len(victory_keys),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate v8 step labeling output.")
    parser.add_argument("--data-dir", required=True, help="Collection run root.")
    parser.add_argument("--output", required=True, help="v8_steps output directory.")
    parser.add_argument("--report", default=None, help="Optional QA report JSON path.")
    parser.add_argument(
        "--include-non-victory",
        action="store_true",
        help="Validate all sequences, not only Victory.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 when validation fails.",
    )
    args = parser.parse_args()

    report = validate_v8_output(
        Path(args.data_dir),
        Path(args.output),
        require_victory=not args.include_non_victory,
    )
    report_path = Path(args.report or Path(args.output) / "v8_qa.json")
    write_json(report_path, report)

    print(f"passed={report['passed']}")
    print(report["totals"])
    if report["invalid_md_samples"]:
        print(f"invalid_md_samples={len(report['invalid_md_samples'])}")
    if report["missing_md_samples"]:
        print(f"missing_md_samples={len(report['missing_md_samples'])}")
    print(f"report={report_path}")

    if args.strict and not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
