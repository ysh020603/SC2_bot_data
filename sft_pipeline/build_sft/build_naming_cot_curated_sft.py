"""Merge Naming CoT sources, dedupe by prompt (smallest model wins), cap ~3 samples per class."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sft_pipeline.build_sft.inject_cot_sft import _rebuild_kept_samples_from_audit
from sft_pipeline.build_sft.templates import assistant_value
from sft_pipeline.common.io import read_json, write_json


MODEL_RANK = {
    "4b": 0,
    "14b": 1,
    "32b": 2,
}


def prompt_key(sample: dict[str, Any]) -> str:
    return next(c["value"] for c in sample["conversations"] if c["from"] == "human")


def extract_cot(gpt_value: str) -> str:
    match = re.search(r"<think>\s*(.*?)\s*</think>", gpt_value, flags=re.S)
    return match.group(1).strip() if match else ""


def answer_type_set(sample: dict[str, Any]) -> frozenset[str]:
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    gpt = re.sub(r"<think>.*?</think>\s*", "", gpt, flags=re.S)
    payload = json.loads(gpt)
    return frozenset(str(item["name"]) for item in payload.get("items", []))


def answer_type_set_any(obj: Any) -> frozenset[str]:
    if isinstance(obj, list):
        items = obj
    else:
        payload = json.loads(obj) if isinstance(obj, str) else obj
        items = payload.get("items", [])
    return frozenset(str(item["name"]) for item in items)


def has_valid_cot(sample: dict[str, Any]) -> bool:
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    return bool(extract_cot(gpt))


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def infer_model_rank(model_key: str | None, fallback: str) -> str:
    key = (model_key or "").lower()
    if "4b" in key:
        return "4b"
    if "14b" in key:
        return "14b"
    return fallback


@dataclass
class TaggedSample:
    sample: dict[str, Any]
    model: str
    model_rank: int
    source: str
    prompt: str
    type_set: frozenset[str]


def load_sft_source(path: Path, *, model: str, source: str) -> list[TaggedSample]:
    if not path.exists():
        return []
    rows: list[TaggedSample] = []
    for sample in read_json(path):
        if not has_valid_cot(sample):
            continue
        rows.append(
            TaggedSample(
                sample=sample,
                model=model,
                model_rank=MODEL_RANK[model],
                source=source,
                prompt=prompt_key(sample),
                type_set=answer_type_set(sample),
            )
        )
    return rows


def load_audit_source(
    audit_file: Path,
    thinking_file: Path,
    *,
    model: str,
    source: str,
) -> list[TaggedSample]:
    if not audit_file.exists() or not thinking_file.exists():
        return []
    samples = read_json(thinking_file)
    kept = _rebuild_kept_samples_from_audit(audit_file, samples, "naming")
    rows: list[TaggedSample] = []
    for sample in kept:
        if not has_valid_cot(sample):
            continue
        rows.append(
            TaggedSample(
                sample=sample,
                model=model,
                model_rank=MODEL_RANK[model],
                source=source,
                prompt=prompt_key(sample),
                type_set=answer_type_set(sample),
            )
        )
    return rows


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


def load_laststep_cot_source(
    qa_jsonl: Path,
    *,
    min_tasks: int,
    max_tasks: int,
    source: str = "laststep_8_20",
) -> list[TaggedSample]:
    if not qa_jsonl.exists():
        return []
    rows: list[TaggedSample] = []
    with qa_jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("agent") != "naming":
                continue
            items = parse_laststep_items(record)
            cot = str(record.get("cot") or "").strip()
            if not items or not cot:
                continue
            total = task_count(items)
            if total < min_tasks or total > max_tasks:
                continue
            prompt_msgs = record.get("prompt") or []
            system = next(msg["content"] for msg in prompt_msgs if msg.get("role") == "system")
            user = next(msg["content"] for msg in prompt_msgs if msg.get("role") == "user")
            answer = json.dumps({"items": items}, ensure_ascii=False)
            sample = {
                "system": system,
                "conversations": [
                    {"from": "human", "value": user},
                    {"from": "gpt", "value": assistant_value(answer, "thinking", cot)},
                ],
            }
            model = infer_model_rank(record.get("model_key"), "32b")
            rows.append(
                TaggedSample(
                    sample=sample,
                    model=model,
                    model_rank=MODEL_RANK[model],
                    source=source,
                    prompt=user,
                    type_set=answer_type_set_any(items),
                )
            )
    return rows


def dedupe_by_prompt(rows: list[TaggedSample]) -> tuple[list[TaggedSample], dict[str, int]]:
    best: dict[str, TaggedSample] = {}
    dropped_same_prompt = 0
    for row in rows:
        existing = best.get(row.prompt)
        if existing is None:
            best[row.prompt] = row
            continue
        if row.model_rank < existing.model_rank:
            best[row.prompt] = row
            dropped_same_prompt += 1
        else:
            dropped_same_prompt += 1
    return list(best.values()), {"duplicate_prompt_dropped": dropped_same_prompt}


def select_by_class(
    rows: list[TaggedSample],
    *,
    per_class_target: int,
) -> tuple[list[TaggedSample], dict[str, Any]]:
    by_class: dict[frozenset[str], list[TaggedSample]] = defaultdict(list)
    for row in rows:
        by_class[row.type_set].append(row)

    selected: list[TaggedSample] = []
    class_stats: list[dict[str, Any]] = []
    cap_dropped = 0

    for type_set, group in sorted(by_class.items(), key=lambda item: sorted(item[0])):
        group_sorted = sorted(
            group,
            key=lambda r: (
                0 if r.source.startswith("laststep") else 1,
                r.prompt,
            ),
        )
        kept = group_sorted if len(group_sorted) <= per_class_target else group_sorted[:per_class_target]
        cap_dropped += max(0, len(group_sorted) - len(kept))
        selected.extend(kept)
        class_stats.append(
            {
                "types": sorted(type_set),
                "available_after_dedupe": len(group_sorted),
                "selected": len(kept),
                "capped": len(group_sorted) > per_class_target,
            }
        )

    report = {
        "unique_classes": len(by_class),
        "classes_capped": sum(1 for row in class_stats if row["capped"]),
        "classes_under_target": sum(1 for row in class_stats if row["selected"] < per_class_target),
        "cap_dropped_samples": cap_dropped,
        "per_class_target": per_class_target,
    }
    return selected, report | {"classes": class_stats}


def default_sources(run_dir: Path) -> list[tuple[str, Path | None, Path | None, str]]:
    naming = run_dir / "sft_agent_aligned/naming/sc2_naming_qwen3_thinking_sft.json"
    gap = run_dir / "naming_cot_gap/input/sc2_naming_qwen3_thinking_sft_missing_cot.json"
    priority = (
        run_dir
        / "naming_cot_gap/priority_missing_classes/sc2_naming_qwen3_thinking_missing_or_sparse_class_sft.json"
    )
    r2 = run_dir / "naming_cot_gap/priority_below_target_r2/sc2_naming_qwen3_thinking_below_target_class_sft.json"
    return [
        (
            "4b",
            run_dir
            / "sft_agent_aligned_cot_qwen3-4b/naming/sc2_naming_qwen3_thinking_cot_Qwen3-4b_think_checked_by_Qwen35-27b_sft.json",
            None,
            "cot_4b_full",
        ),
        (
            "14b",
            run_dir
            / "sft_agent_aligned_cot_qwen3-14b/naming/sc2_naming_qwen3_thinking_cot_Qwen3-14b_think_checked_by_Qwen35-27b_sft.json",
            None,
            "cot_14b_full",
        ),
        (
            "32b",
            run_dir
            / "sft_agent_aligned_cot_qwen3-32b/naming/sc2_naming_qwen3_thinking_cot_Qwen3-32b_think_checked_by_Qwen3-32b_sft.json",
            None,
            "cot_32b_full",
        ),
        (
            "32b",
            run_dir
            / "sft_agent_aligned_cot_qwen3-32b_naming_gap/naming/sc2_naming_qwen3_thinking_cot_Qwen3-32b_think_checked_by_Qwen3-32b_sft.json",
            None,
            "cot_32b_gap",
        ),
        (
            "32b",
            run_dir
            / "sft_agent_aligned_cot_qwen3-32b_naming_priority/naming/sc2_naming_qwen3_thinking_cot_Qwen3-32b_think_checked_by_rule_only_sft.json",
            None,
            "cot_32b_priority",
        ),
        (
            "32b",
            None,
            run_dir / "sft_agent_aligned_cot_qwen3-32b_naming_priority_r2/naming/cot_audit.jsonl",
            "cot_32b_priority_r2",
        ),
    ]


def build_curated_naming_cot(
    run_dir: Path,
    *,
    per_class_target: int = 3,
    laststep_qa: Path | None = None,
    laststep_min_tasks: int = 8,
    laststep_max_tasks: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows: list[TaggedSample] = []
    source_counts: Counter[str] = Counter()

    for model, sft_path, audit_path, source in default_sources(run_dir):
        if sft_path is not None:
            rows = load_sft_source(sft_path, model=model, source=source)
        else:
            thinking = run_dir / "naming_cot_gap/priority_below_target_r2/sc2_naming_qwen3_thinking_below_target_class_sft.json"
            rows = load_audit_source(audit_path, thinking, model=model, source=source)  # type: ignore[arg-type]
        source_counts[source] = len(rows)
        all_rows.extend(rows)

    qa_path = laststep_qa or Path(
        "SC2-Agent-260510/game_records/qwen_think_hybrid_v7_terran_sweep_last_step_victory_qa.jsonl"
    )
    laststep_rows = load_laststep_cot_source(
        qa_path,
        min_tasks=laststep_min_tasks,
        max_tasks=laststep_max_tasks,
    )
    source_counts["laststep_8_20"] = len(laststep_rows)
    all_rows.extend(laststep_rows)

    deduped, dedupe_report = dedupe_by_prompt(all_rows)
    selected, class_report = select_by_class(deduped, per_class_target=per_class_target)

    manifest = []
    for idx, row in enumerate(selected):
        manifest.append(
            {
                "index": idx,
                "source": row.source,
                "model": row.model,
                "types": sorted(row.type_set),
                "prompt_hash": prompt_hash(row.prompt),
            }
        )

    model_dist = Counter(row.model for row in selected)
    source_dist = Counter(row.source for row in selected)

    report: dict[str, Any] = {
        "run_dir": str(run_dir.resolve()),
        "rules": {
            "dedupe_key": "human prompt",
            "model_preference": "smallest model on duplicate prompt (4b < 14b < 32b)",
            "class_definition": "frozenset(items[].name)",
            "per_class_target": per_class_target,
            "under_target_policy": "keep all",
        },
        "source_loaded": dict(source_counts),
        "totals": {
            "loaded_with_cot": len(all_rows),
            "after_prompt_dedupe": len(deduped),
            "final_selected": len(selected),
        },
        "dedupe": dedupe_report,
        "class_selection": {
            "unique_classes": class_report["unique_classes"],
            "classes_capped": class_report["classes_capped"],
            "classes_under_target": class_report["classes_under_target"],
            "cap_dropped_samples": class_report["cap_dropped_samples"],
        },
        "final_model_distribution": dict(model_dist),
        "final_source_distribution": dict(source_dist),
    }

    samples = [row.sample for row in selected]
    return samples, report | {"manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build curated Naming CoT SFT dataset.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Default: <run-dir>/sft_agent_aligned/naming/curated_cot/merged/sc2_naming_qwen3_thinking_cot_curated_sft.json",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--per-class-target", type=int, default=3)
    parser.add_argument("--laststep-qa", type=Path, default=None)
    parser.add_argument("--laststep-min-tasks", type=int, default=8)
    parser.add_argument("--laststep-max-tasks", type=int, default=20)
    args = parser.parse_args()

    out_dir = args.run_dir / "sft_agent_aligned/naming/curated_cot/merged"
    output = args.output or out_dir / "sc2_naming_qwen3_thinking_cot_curated_sft.json"
    report_path = args.report or out_dir / "build_report.json"
    manifest_path = args.manifest or out_dir / "sample_manifest.jsonl"

    samples, report = build_curated_naming_cot(
        args.run_dir,
        per_class_target=args.per_class_target,
        laststep_qa=args.laststep_qa,
        laststep_min_tasks=args.laststep_min_tasks,
        laststep_max_tasks=args.laststep_max_tasks,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, samples)
    manifest_rows = report.pop("manifest")
    write_json(report_path, report)
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in manifest_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    class_groups_path = out_dir / "class_groups.json"
    grouped: dict[str, dict[str, Any]] = {}
    for row in manifest_rows:
        key = "|".join(row["types"])
        grouped.setdefault(key, {"types": row["types"], "indices": [], "sources": []})
        grouped[key]["indices"].append(row["index"])
        grouped[key]["sources"].append(row["source"])
    for entry in grouped.values():
        entry["count"] = len(entry["indices"])
    write_json(class_groups_path, dict(sorted(grouped.items())))

    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))
    print(f"wrote {len(samples)} samples -> {output}")


if __name__ == "__main__":
    main()
