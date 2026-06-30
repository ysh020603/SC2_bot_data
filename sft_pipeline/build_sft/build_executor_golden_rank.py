"""Annotate executor QA records with rule-based golden producer rankings."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sft_pipeline.common.executor_golden_rank import parse_executor_prompt, rank_executor_candidates
from sft_pipeline.common.io import write_json, write_jsonl

# Fields dropped from output (original LLM inference artifacts).
_DROP_OUTPUT_FIELDS = frozenset({
    "answer",
    "cot",
    "raw_content",
    "llm_error",
    "is_reasoning",
    "reasoning_source",
    "reasoning_extract_mode",
    "prompt",
})


def _iter_records(payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    by_strategy = payload.get("by_strategy")
    if isinstance(by_strategy, dict):
        for strategy, records in by_strategy.items():
            if not isinstance(records, list):
                continue
            for record in records:
                if isinstance(record, dict):
                    yield strategy, record
        return
    records = payload.get("records")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict):
                yield str(record.get("strategy") or ""), record


def _prompt_text(record: dict[str, Any]) -> tuple[str, str]:
    system = record.get("system") or ""
    user = record.get("user") or ""
    if not system and isinstance(record.get("prompt"), list):
        for msg in record["prompt"]:
            if msg.get("role") == "system":
                system = msg.get("content") or ""
            elif msg.get("role") == "user":
                user = msg.get("content") or ""
    return system, user


def annotate_record(record: dict[str, Any]) -> dict[str, Any]:
    system, user = _prompt_text(record)
    ctx = parse_executor_prompt(system, user)
    result = rank_executor_candidates(ctx)

    annotated = {key: value for key, value in record.items() if key not in _DROP_OUTPUT_FIELDS}
    annotated["system"] = system
    annotated["user"] = user
    annotated["ability"] = ctx.ability
    annotated["golden_tags"] = result.golden_tags
    annotated["golden_rank"] = result.to_dict()
    return annotated


def slim_record(record: dict[str, Any]) -> dict[str, Any]:
    """Minimal SFT-oriented view: prompt + golden tags only."""
    return {
        "system": record.get("system") or "",
        "user": record.get("user") or "",
        "golden_tags": list(record.get("golden_tags") or []),
    }


def build_report(
  annotated_by_strategy: dict[str, list[dict[str, Any]]],
  *,
  input_path: Path,
  output_dir: Path,
) -> dict[str, Any]:
    stats: Counter[str] = Counter()
    by_strategy_stats: dict[str, dict[str, Any]] = {}

    for strategy, records in annotated_by_strategy.items():
        strat_stats: Counter[str] = Counter()
        for record in records:
            stats["records_total"] += 1
            strat_stats["records"] += 1
            if len(record.get("golden_tags") or []) > 1:
                stats["multi_golden"] += 1
                strat_stats["multi_golden"] += 1
            golden = record.get("golden_rank") or {}
            if golden.get("reservation_active"):
                stats["reservation_active"] += 1
                strat_stats["reservation_active"] += 1
            if golden.get("fallback_no_eligible"):
                stats["fallback_no_eligible"] += 1
                strat_stats["fallback_no_eligible"] += 1
        by_strategy_stats[strategy] = {
            "records": strat_stats["records"],
            "multi_golden": strat_stats["multi_golden"],
            "reservation_active": strat_stats["reservation_active"],
            "fallback_no_eligible": strat_stats["fallback_no_eligible"],
        }

    total = stats["records_total"]
    return {
        "input": str(input_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records_total": total,
        "multi_golden": stats["multi_golden"],
        "multi_golden_rate": round(stats["multi_golden"] / total, 4) if total else 0.0,
        "reservation_active": stats["reservation_active"],
        "fallback_no_eligible": stats["fallback_no_eligible"],
        "by_strategy": by_strategy_stats,
    }


def annotate_executor_qa_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    annotated_by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for strategy, record in _iter_records(payload):
        annotated_by_strategy[strategy].append(annotate_record(record))

    output_payload = dict(payload)
    metadata = dict(output_payload.get("metadata") or {})
    metadata["golden_rank_extractor"] = "sft_pipeline.common.executor_golden_rank"
    metadata["golden_rank_annotator"] = "sft_pipeline.build_sft.build_executor_golden_rank"
    metadata["golden_rank_doc"] = "sft_pipeline/build_sft/executor_golden_rank.md"
    metadata["golden_rank_generated_at"] = datetime.now(timezone.utc).isoformat()
    metadata.pop("description", None)
    metadata["description"] = (
        "Executor prompts with rule-based golden_tags; original LLM answers are not retained."
    )
    output_payload["metadata"] = metadata
    output_payload["by_strategy"] = dict(annotated_by_strategy)
    return output_payload, dict(annotated_by_strategy)


def annotate_executor_qa_file(input_path: Path, output_dir: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output_payload, annotated_by_strategy = annotate_executor_qa_payload(payload)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_json = output_dir / "executor_qa_golden.json"
    output_jsonl = output_dir / "executor_qa_golden.jsonl"
    output_json_slim = output_dir / "executor_qa_golden_slim.json"
    output_jsonl_slim = output_dir / "executor_qa_golden_slim.jsonl"
    report_path = output_dir / "build_report.json"

    flat_rows = [row for rows in annotated_by_strategy.values() for row in rows]
    slim_by_strategy = {
        strategy: [slim_record(row) for row in rows]
        for strategy, rows in annotated_by_strategy.items()
    }
    slim_payload = {
        "metadata": {
            **dict(output_payload.get("metadata") or {}),
            "format": "slim",
            "fields": ["system", "user", "golden_tags"],
            "description": "Executor golden labels only; system + user + golden_tags per record.",
        },
        "by_strategy": slim_by_strategy,
    }
    slim_flat_rows = [slim_record(row) for row in flat_rows]

    write_json(output_json, output_payload)
    write_jsonl(output_jsonl, flat_rows)
    write_json(output_json_slim, slim_payload)
    write_jsonl(output_jsonl_slim, slim_flat_rows)
    report = build_report(annotated_by_strategy, input_path=input_path, output_dir=output_dir)
    report["outputs"] = {
        "full_json": str(output_json.resolve()),
        "full_jsonl": str(output_jsonl.resolve()),
        "slim_json": str(output_json_slim.resolve()),
        "slim_jsonl": str(output_jsonl_slim.resolve()),
    }
    write_json(report_path, report)
    return report


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    default_input = (
        repo_root
        / "SC2-Agent-260510/game_records/qwen17b_grpo_naming_27b_exec_10strat_macro_r5/executor_qa_extract.json"
    )
    default_output = repo_root / "sft_pipeline_outputs/executor_golden_rank/qwen17b_grpo_naming_27b_exec_10strat_macro_r5"

    parser = argparse.ArgumentParser(description="Annotate executor QA with golden producer rankings.")
    parser.add_argument("--input", type=Path, default=default_input, help="executor_qa_extract.json path")
    parser.add_argument("--output-dir", type=Path, default=default_output, help="Output directory")
    args = parser.parse_args()

    report = annotate_executor_qa_file(args.input.resolve(), args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
