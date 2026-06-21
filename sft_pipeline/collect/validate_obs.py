from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import iter_sequence_files, read_json, write_json


def validate_run(path: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    totals = {
        "sequence_files": 0,
        "actions": 0,
        "missing_obs_text": 0,
        "missing_obs_structured": 0,
        "order_mismatch": 0,
        "executor_context_train_multi": 0,
    }

    for seq_path in iter_sequence_files(path):
        data = read_json(seq_path)
        sequence = data.get("sequence") or []
        order_list = data.get("order_list") or []
        totals["sequence_files"] += 1
        totals["actions"] += len(sequence)

        file_report = {"path": str(seq_path), "issues": []}
        if [entry.get("ability") for entry in sequence] != order_list:
            totals["order_mismatch"] += 1
            file_report["issues"].append("order_list does not match sequence abilities")

        for entry in sequence:
            obs = entry.get("obs") or {}
            if not obs.get("text"):
                totals["missing_obs_text"] += 1
            if not obs.get("structured"):
                totals["missing_obs_structured"] += 1
            ctx = entry.get("executor_context")
            if ctx and len(ctx.get("candidate_executors") or []) > 1:
                totals["executor_context_train_multi"] += 1

        if file_report["issues"]:
            reports.append(file_report)

    return {"totals": totals, "files_with_issues": reports}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate collected SC2 sequence obs fields.")
    parser.add_argument("--run", required=True, help="Collection run root or sequence JSON.")
    parser.add_argument("--output", default=None, help="Optional QA report path.")
    args = parser.parse_args()

    report = validate_run(Path(args.run))
    if args.output:
        write_json(args.output, report)
    print(report["totals"])


if __name__ == "__main__":
    main()

