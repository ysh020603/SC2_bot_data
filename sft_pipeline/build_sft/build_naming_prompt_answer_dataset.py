"""Build a plain prompt/answer Naming dataset (no CoT, not ShareGPT)."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import read_json, write_json


def strip_thinking(gpt_value: str) -> str:
    return re.sub(r"<think>.*?</think>\s*", "", gpt_value, flags=re.S).strip()


def parse_answer(gpt_value: str) -> dict[str, Any]:
    return json.loads(strip_thinking(gpt_value))


def sharegpt_to_record(
    sample: dict[str, Any],
    *,
    source: str,
    is_last_step: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system = sample.get("system", "")
    user = next(c["value"] for c in sample["conversations"] if c["from"] == "human")
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    record: dict[str, Any] = {
        "source": source,
        "is_last_step": is_last_step,
        "system": system,
        "user": user,
        "prompt": {"system": system, "user": user},
        "answer": parse_answer(gpt),
    }
    if extra:
        record.update(extra)
    return record


def task_count(items: list[dict[str, Any]]) -> int:
    return sum(int(item.get("count", 1)) for item in items)


def parse_laststep_items(record: dict[str, Any]) -> list[dict[str, Any]] | None:
    pipeline = record.get("pipeline_named_items")
    if isinstance(pipeline, list) and pipeline:
        return pipeline
    answer = record.get("answer")
    if not isinstance(answer, str):
        return None
    try:
        items = json.loads(answer).get("items")
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list) or not items:
        return None
    return items


def load_pipeline_records(nothink_sft: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, sample in enumerate(read_json(nothink_sft)):
        rows.append(
            sharegpt_to_record(
                sample,
                source="pipeline",
                is_last_step=False,
                extra={"pipeline_index": index},
            )
        )
    return rows


def load_laststep_records(
    qa_jsonl: Path,
    *,
    min_tasks: int | None = None,
    max_tasks: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    task_dist: Counter[int] = Counter()
    skipped: Counter[str] = Counter()

    with qa_jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("agent") != "naming":
                continue
            items = parse_laststep_items(record)
            if not items:
                skipped["parse_fail"] += 1
                continue
            total = task_count(items)
            if min_tasks is not None and total < min_tasks:
                skipped["task_out_of_range"] += 1
                continue
            if max_tasks is not None and total > max_tasks:
                skipped["task_out_of_range"] += 1
                continue

            prompt_msgs = record.get("prompt") or []
            system = next(msg["content"] for msg in prompt_msgs if msg.get("role") == "system")
            user = next(msg["content"] for msg in prompt_msgs if msg.get("role") == "user")
            sample = {
                "system": system,
                "conversations": [
                    {"from": "human", "value": user},
                    {"from": "gpt", "value": json.dumps({"items": items}, ensure_ascii=False)},
                ],
            }
            rows.append(
                sharegpt_to_record(
                    sample,
                    source="laststep_qa",
                    is_last_step=True,
                    extra={
                        "record_id": record.get("record_id"),
                        "pair_id": record.get("pair_id"),
                        "strategy": record.get("strategy"),
                        "task_count": total,
                    },
                )
            )
            task_dist[total] += 1

    report = {
        "kept": len(rows),
        "skipped": dict(skipped),
        "task_count_distribution": {str(k): v for k, v in sorted(task_dist.items())},
    }
    return rows, report


def build_dataset(
    run_dir: Path,
    *,
    laststep_qa: Path | None = None,
    laststep_min_tasks: int | None = None,
    laststep_max_tasks: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    nothink_sft = run_dir / "sft_agent_aligned/naming/sc2_naming_qwen3_nothink_sft.json"
    qa_path = laststep_qa or Path(
        "SC2-Agent-260510/game_records/qwen_think_hybrid_v7_terran_sweep_last_step_victory_qa.jsonl"
    )

    pipeline_rows = load_pipeline_records(nothink_sft)
    laststep_rows, laststep_report = load_laststep_records(
        qa_path,
        min_tasks=laststep_min_tasks,
        max_tasks=laststep_max_tasks,
    )

    pipeline_users = {row["user"] for row in pipeline_rows}
    overlap = sum(1 for row in laststep_rows if row["user"] in pipeline_users)

    merged = pipeline_rows + laststep_rows
    report = {
        "run_dir": str(run_dir.resolve()),
        "sources": {
            "pipeline_nothink_sft": str(nothink_sft.resolve()),
            "laststep_qa_jsonl": str(qa_path.resolve()),
        },
        "filters": {
            "laststep_agent": "naming",
            "laststep_task_count_min": laststep_min_tasks,
            "laststep_task_count_max": laststep_max_tasks,
            "laststep_task_count_note": "null = no limit; all naming QA rows kept",
        },
        "counts": {
            "pipeline": len(pipeline_rows),
            "laststep": len(laststep_rows),
            "total": len(merged),
            "laststep_user_overlap_with_pipeline": overlap,
        },
        "laststep": laststep_report,
        "schema": {
            "source": "pipeline | laststep_qa",
            "is_last_step": "true for last-step QA rows only",
            "system": "Naming agent system instruction",
            "user": "Observation + strategy step user message",
            "prompt": "object with system and user strings",
            "answer": "object with items: [{name, count}, ...]",
        },
    }
    return merged, report


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build plain Naming prompt/answer dataset.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Default: <run-dir>/naming_prompt_answer",
    )
    parser.add_argument("--laststep-qa", type=Path, default=None)
    parser.add_argument(
        "--laststep-min-tasks",
        type=int,
        default=None,
        help="Optional lower bound on sum(items[].count); default keeps all",
    )
    parser.add_argument(
        "--laststep-max-tasks",
        type=int,
        default=None,
        help="Optional upper bound on sum(items[].count); default keeps all",
    )
    args = parser.parse_args()

    out_dir = args.output_dir or (args.run_dir / "naming_prompt_answer")
    rows, report = build_dataset(
        args.run_dir,
        laststep_qa=args.laststep_qa,
        laststep_min_tasks=args.laststep_min_tasks,
        laststep_max_tasks=args.laststep_max_tasks,
    )

    write_jsonl(out_dir / "naming_prompt_answer.jsonl", rows)
    write_json(out_dir / "naming_prompt_answer.json", rows)
    write_json(out_dir / "build_report.json", report)

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(f"wrote {len(rows)} records -> {out_dir}")


if __name__ == "__main__":
    main()
