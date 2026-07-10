"""Export resampled ordering ShareGPT data as standard nocot SFT package."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import read_json, write_json
from sft_pipeline.resample.ordering.resample_ordering_sft import parse_ordering_sample


def validate_nothink_sample(index: int, sample: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if "system" not in sample:
        issues.append("missing system")
    conversations = sample.get("conversations") or []
    if len(conversations) < 2:
        issues.append("missing conversations")
        return issues
    gpt = next((c.get("value") for c in conversations if c.get("from") == "gpt"), "")
    if not isinstance(gpt, str) or not gpt.strip():
        issues.append("empty assistant")
    elif "<think>" in gpt:
        issues.append("assistant contains thinking block")
    else:
        try:
            answer = json.loads(gpt.strip())
            if not isinstance(answer.get("ordered_actions"), list):
                issues.append("ordered_actions missing")
        except json.JSONDecodeError:
            issues.append("assistant is not valid JSON")
    return issues


def ordering_dataset_info_fragment(file_name: str) -> dict[str, Any]:
    name = "sc2_ordering_qwen3_nothink_sft"
    return {
        name: {
            "file_name": file_name,
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "system": "system"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
    }


def export_ordering_nothink(
    input_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    samples = read_json(input_path)
    if not isinstance(samples, list):
        raise ValueError("input must be a JSON array of ShareGPT samples")

    issues_by_index: dict[int, list[str]] = {}
    parsed_rows: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        sample_issues = validate_nothink_sample(index, sample)
        if sample_issues:
            issues_by_index[index] = sample_issues
            continue
        try:
            parsed_rows.append(parse_ordering_sample(index, sample))
        except Exception as exc:
            issues_by_index[index] = [str(exc)]

    if issues_by_index:
        raise ValueError(f"{len(issues_by_index)} invalid samples; first: {next(iter(issues_by_index.items()))}")

    task_dir = output_dir / "ordering"
    task_dir.mkdir(parents=True, exist_ok=True)
    out_file = task_dir / "sc2_ordering_qwen3_nothink_sft.json"
    write_json(out_file, samples)

    step_dist = Counter(row["step"] for row in parsed_rows)
    multiset_dist = Counter(row["multiset"] for row in parsed_rows)
    qa_report = {
        "ordering": {
            "nothink": {
                "total": len(samples),
                "kept": len(samples),
                "dropped_invalid": 0,
                "thinking_samples": 0,
                "unique_multisets": len(multiset_dist),
                "step_distribution": {str(step): count for step, count in sorted(step_dist.items())},
                "source": str(input_path.resolve()),
                "file": str(out_file.resolve()),
            }
        }
    }
    write_json(output_dir / "qa_report.json", qa_report)
    write_json(
        output_dir / "dataset_info.fragment.json",
        ordering_dataset_info_fragment(out_file.name),
    )
    return qa_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Export ordering resampled JSON as nocot SFT package.")
    parser.add_argument("--input", type=Path, required=True, help="Resampled ordering ShareGPT JSON.")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output root (writes ordering/ + dataset_info.fragment.json + qa_report.json).",
    )
    args = parser.parse_args()
    report = export_ordering_nothink(args.input, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
