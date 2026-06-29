"""Extract pending Naming samples for classes with no or sparse CoT coverage."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def prompt_key(sample: dict[str, Any]) -> str:
    return next(c["value"] for c in sample["conversations"] if c["from"] == "human")


def gold_types_sample(sample: dict[str, Any]) -> frozenset[str]:
    gpt = next(c["value"] for c in sample["conversations"] if c["from"] == "gpt")
    gpt = re.sub(r"<think>.*?</think>\s*", "", gpt, flags=re.S)
    items = json.loads(gpt).get("items", [])
    return frozenset(str(item["name"]) for item in items)


def gold_types_any(obj: Any) -> frozenset[str]:
    payload = json.loads(obj) if isinstance(obj, str) else obj
    items = payload.get("items", [])
    return frozenset(str(item["name"]) for item in items)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_sft(path: Path, class_cot_prompts: dict[frozenset[str], set[str]]) -> None:
    for sample in load_json(path):
        cls = gold_types_sample(sample)
        class_cot_prompts[cls].add(prompt_key(sample))


def ingest_audit(
    path: Path,
    idx_map: dict[int, str],
    class_cot_prompts: dict[frozenset[str], set[str]],
) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("generated_cot"):
            continue
        prompt = idx_map.get(int(row["index"]))
        if prompt:
            class_cot_prompts[gold_types_any(row["gold_answer"])].add(prompt)


def ingest_teacher_drop(
    path: Path,
    idx_map: dict[int, str],
    class_cot_prompts: dict[frozenset[str], set[str]],
) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("stage") != "teacher_drop" or not row.get("generated_cot"):
            continue
        prompt = idx_map.get(int(row["index"]))
        if prompt:
            class_cot_prompts[gold_types_any(row["gold_answer"])].add(prompt)


def build_cot_index(run_dir: Path) -> dict[frozenset[str], set[str]]:
    full = load_json(run_dir / "sft_agent_aligned/naming/sc2_naming_qwen3_thinking_sft.json")
    pending = load_json(run_dir / "naming_cot_gap/input/sc2_naming_qwen3_thinking_sft_missing_cot.json")
    priority_subset = run_dir / "naming_cot_gap/priority_missing_classes/sc2_naming_qwen3_thinking_missing_or_sparse_class_sft.json"
    full_idx = {i: prompt_key(s) for i, s in enumerate(full)}
    gap_idx = {i: prompt_key(s) for i, s in enumerate(pending)}
    priority_idx: dict[int, str] = {}
    if priority_subset.exists():
        priority_idx = {i: prompt_key(s) for i, s in enumerate(load_json(priority_subset))}

    class_cot_prompts: dict[frozenset[str], set[str]] = defaultdict(set)
    for sub, filename in [
        ("32b", "sc2_naming_qwen3_thinking_cot_Qwen3-32b_think_checked_by_Qwen3-32b_sft.json"),
        ("14b", "sc2_naming_qwen3_thinking_cot_Qwen3-14b_think_checked_by_Qwen35-27b_sft.json"),
        ("4b", "sc2_naming_qwen3_thinking_cot_Qwen3-4b_think_checked_by_Qwen35-27b_sft.json"),
    ]:
        ingest_sft(run_dir / f"sft_agent_aligned_cot_qwen3-{sub}/naming/{filename}", class_cot_prompts)

    for path, idx in [
        (run_dir / "sft_agent_aligned_cot_qwen3-32b/naming/cot_rejected_detail.jsonl", full_idx),
        (run_dir / "sft_agent_aligned_cot_qwen3-14b/naming/cot_rejected_detail.jsonl", full_idx),
        (run_dir / "sft_agent_aligned_cot_qwen3-4b/naming/cot_rejected_detail.jsonl", full_idx),
        (run_dir / "sft_agent_aligned_cot_qwen3-32b_naming_gap/naming/cot_rejected_detail.jsonl", gap_idx),
    ]:
        ingest_teacher_drop(path, idx, class_cot_prompts)

    ingest_audit(
        run_dir / "sft_agent_aligned_cot_qwen3-32b_naming_gap/naming/cot_audit.jsonl",
        gap_idx,
        class_cot_prompts,
    )
    if priority_idx:
        ingest_audit(
            run_dir / "sft_agent_aligned_cot_qwen3-32b_naming_priority/naming/cot_audit.jsonl",
            priority_idx,
            class_cot_prompts,
        )
    return class_cot_prompts


def class_target_count(pending_samples: int, *, target_min: int = 2) -> int:
    if pending_samples < target_min:
        return pending_samples
    return target_min


def extract_below_target(
    run_dir: Path,
    *,
    source_subset: Path,
    target_min: int = 2,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    samples = load_json(source_subset)
    class_cot_prompts = build_cot_index(run_dir)
    class_cot_count = {cls: len(prompts) for cls, prompts in class_cot_prompts.items()}

    class_pending = Counter(gold_types_sample(s) for s in samples)
    class_stats: list[dict[str, Any]] = []
    for cls in class_pending:
        cot_n = class_cot_count.get(cls, 0)
        target = class_target_count(class_pending[cls], target_min=target_min)
        class_stats.append(
            {
                "types": sorted(cls),
                "type_count": len(cls),
                "pending_samples": class_pending[cls],
                "cot_prompts": cot_n,
                "target": target,
                "below_target": cot_n < target,
            }
        )
    class_stats.sort(key=lambda row: (-row["pending_samples"], row["types"]))

    extracted: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for source_index, sample in enumerate(samples):
        cls = gold_types_sample(sample)
        cot_n = class_cot_count.get(cls, 0)
        target = class_target_count(class_pending[cls], target_min=target_min)
        if cot_n >= target:
            continue
        extracted.append(sample)
        manifest.append(
            {
                "subset_index": len(extracted) - 1,
                "source_index": source_index,
                "types": sorted(cls),
                "cot_prompts_for_class": cot_n,
                "target": target,
            }
        )

    below_classes = sum(1 for row in class_stats if row["below_target"])
    report = {
        "target_min": target_min,
        "source_subset": str(source_subset.resolve()),
        "source_samples": len(samples),
        "source_unique_classes": len(class_pending),
        "below_target_classes": below_classes,
        "extracted_samples": len(extracted),
        "extracted_unique_classes": len({tuple(row["types"]) for row in manifest}),
    }

    out = output_dir or (run_dir / "naming_cot_gap/priority_below_target_r2")
    out.mkdir(parents=True, exist_ok=True)
    (out / "class_cot_distribution.json").write_text(
        json.dumps({"report": report, "classes": class_stats}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "sc2_naming_qwen3_thinking_below_target_class_sft.json").write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (out / "extract_manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report["output_dir"] = str(out.resolve())
    return report


def extract_priority(
    run_dir: Path,
    *,
    sparse_max: int,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    pending = load_json(run_dir / "naming_cot_gap/input/sc2_naming_qwen3_thinking_sft_missing_cot.json")
    class_cot_prompts = build_cot_index(run_dir)
    class_cot_count = {cls: len(prompts) for cls, prompts in class_cot_prompts.items()}

    prompt_to_class = {prompt_key(s): gold_types_sample(s) for s in pending}
    pending_class_counter = Counter(prompt_to_class.values())

    class_stats: list[dict[str, Any]] = []
    for cls in pending_class_counter:
        cot_n = class_cot_count.get(cls, 0)
        if cot_n == 0:
            coverage = "none"
        elif cot_n <= sparse_max:
            coverage = "sparse"
        else:
            coverage = "ok"
        class_stats.append(
            {
                "types": sorted(cls),
                "type_count": len(cls),
                "pending_samples": pending_class_counter[cls],
                "cot_prompts": cot_n,
                "coverage": coverage,
            }
        )
    class_stats.sort(key=lambda row: (-row["pending_samples"], row["types"]))

    extracted: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for gap_index, sample in enumerate(pending):
        cls = prompt_to_class[prompt_key(sample)]
        cot_n = class_cot_count.get(cls, 0)
        if cot_n > sparse_max:
            continue
        coverage = "none" if cot_n == 0 else "sparse"
        extracted.append(sample)
        manifest.append(
            {
                "subset_index": len(extracted) - 1,
                "source_gap_index": gap_index,
                "types": sorted(cls),
                "cot_prompts_for_class": cot_n,
                "coverage": coverage,
            }
        )

    cot_classes = [row for row in class_stats if row["cot_prompts"] > 0]
    cot_hist = Counter(row["cot_prompts"] for row in cot_classes)

    report = {
        "sparse_max": sparse_max,
        "pending_samples": len(pending),
        "pending_unique_classes": len(pending_class_counter),
        "classes_zero_cot": sum(1 for row in class_stats if row["coverage"] == "none"),
        "classes_sparse_cot": sum(1 for row in class_stats if row["coverage"] == "sparse"),
        "classes_ok_cot": sum(1 for row in class_stats if row["coverage"] == "ok"),
        "cot_prompt_count_histogram": {str(k): v for k, v in sorted(cot_hist.items())},
        "extracted_samples": len(extracted),
        "extracted_none_samples": sum(1 for row in manifest if row["coverage"] == "none"),
        "extracted_sparse_samples": sum(1 for row in manifest if row["coverage"] == "sparse"),
        "extracted_unique_classes": len({tuple(row["types"]) for row in manifest}),
    }

    out = output_dir or (run_dir / "naming_cot_gap/priority_missing_classes")
    out.mkdir(parents=True, exist_ok=True)
    (out / "class_cot_distribution.json").write_text(
        json.dumps({"report": report, "classes": class_stats}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "sc2_naming_qwen3_thinking_missing_or_sparse_class_sft.json").write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (out / "extract_manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report["output_dir"] = str(out.resolve())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract priority pending Naming classes.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sparse-max", type=int, default=2, help="Classes with <= N CoT prompts are sparse.")
    parser.add_argument("--below-target-only", action="store_true")
    parser.add_argument("--source-subset", type=Path, default=None)
    parser.add_argument("--target-min", type=int, default=2)
    args = parser.parse_args()
    if args.below_target_only:
        subset = args.source_subset or (
            args.run_dir / "naming_cot_gap/priority_missing_classes/sc2_naming_qwen3_thinking_missing_or_sparse_class_sft.json"
        )
        report = extract_below_target(
            args.run_dir,
            source_subset=subset,
            target_min=args.target_min,
            output_dir=args.output_dir,
        )
    else:
        report = extract_priority(args.run_dir, sparse_max=args.sparse_max, output_dir=args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
