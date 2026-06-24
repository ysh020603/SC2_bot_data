from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import iter_sequence_files, read_json, safe_stem, write_json, write_jsonl
from sft_pipeline.label_steps.sequence_order import (
    LLM_FAILED_MARK,
    bot_folder_for_sequence,
    difficulty_from_meta,
    enemy_race_from_meta,
    md_is_valid,
    order_sequences,
    run_ordered_pool,
    victory_sequence_files,
)


ROOT = Path(__file__).resolve().parents[2]
BO_TO_NL_TOOLS = ROOT / "bo_2_nlstep" / "Tools"
if str(BO_TO_NL_TOOLS) not in sys.path:
    sys.path.insert(0, str(BO_TO_NL_TOOLS))

from action_mapper import ActionMapper  # type: ignore  # noqa: E402
import bo_to_doc_v7  # type: ignore  # noqa: E402
from bo_to_doc_v7 import process_trajectory, split_into_steps  # type: ignore  # noqa: E402


STEP_RE = re.compile(r"^\[Step\s+(?P<num>\d+)\]\s*(?P<text>.*)$")


def _parse_md_steps(md_path: Path) -> dict[int, str]:
    steps: dict[int, str] = {}
    if not md_path.exists():
        return steps
    with md_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            match = STEP_RE.match(line.strip())
            if match:
                steps[int(match.group("num"))] = line.strip()
    return steps


def _sample_key(bot_folder: str, seq_path: Path, meta: dict[str, Any]) -> str:
    opponent = str(meta.get("opponent_id") or "unknown")
    map_name = str(meta.get("map") or meta.get("map_engine") or "unknown_map")
    recorded_at = str(meta.get("recorded_at") or seq_path.stem)
    return safe_stem(f"{bot_folder}_{opponent}_{map_name}_{recorded_at}")


def _expected_md_path(md_dir: Path, data_dir: Path, seq_path: Path) -> Path:
    meta = read_json(seq_path).get("meta", {})
    bot_folder = bot_folder_for_sequence(data_dir, seq_path)
    sample_key = _sample_key(bot_folder, seq_path, meta)
    return md_dir / f"{sample_key}.md"


def _rows_from_existing_md(
    data_dir: Path,
    seq_path: Path,
    md_path: Path,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
    seq_data = read_json(seq_path)
    meta = seq_data.get("meta", {})
    order_list = list(seq_data.get("order_list") or [])
    sequence = list(seq_data.get("sequence") or [])
    if not order_list or not sequence:
        return None

    bot_folder = bot_folder_for_sequence(data_dir, seq_path)
    sample_key = _sample_key(bot_folder, seq_path, meta)
    md_steps = _parse_md_steps(md_path)
    step_ranges = split_into_steps(order_list)

    normal_steps: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for idx, (start, end) in enumerate(step_ranges, start=1):
        step_text = md_steps.get(idx, "")
        if not step_text or LLM_FAILED_MARK in step_text:
            continue
        row = {
            "sample_id": f"{sample_key}/step_{idx:03d}",
            "source_sequence_file": str(seq_path.resolve()),
            "md_path": str(md_path.resolve()),
            "bot": bot_folder,
            "bot_name": meta.get("bot_name"),
            "map": meta.get("map"),
            "enemy_race": meta.get("enemy_race"),
            "opponent_id": meta.get("opponent_id"),
            "result": meta.get("result"),
            "step_id": idx,
            "action_range": [start, end],
            "ordered_actions": order_list[start : end + 1],
            "step_text_v7": step_text,
            "step_text_v6": step_text,
            "obs_at_step_start": sequence[start].get("obs", {}),
            "obs_at_each_action": [
                {
                    "seq": entry.get("seq"),
                    "ability": entry.get("ability"),
                    "obs": entry.get("obs", {}),
                    "local_obs": entry.get("local_obs", {}),
                    "executor_context": entry.get("executor_context"),
                }
                for entry in sequence[start : end + 1]
            ],
        }
        rows.append(row)
        normal_steps.append(
            {
                "step": idx,
                "range": [start, end],
                "action_count": end - start + 1,
                "step_text_v7": step_text,
                "step_text_v6": step_text,
            }
        )

    index_entry = {
        "source_sequence_file": str(seq_path.resolve()),
        "md_path": str(md_path.resolve()),
        "total_actions": len(order_list),
        "total_steps": len(normal_steps),
        "steps": normal_steps,
    }
    return sample_key, index_entry, rows


def _write_sequence_order_preview(
    output_dir: Path,
    data_dir: Path,
    md_dir: Path,
    seq_files: list[Path],
    sequence_order: str,
    skip_existing: bool,
) -> None:
    preview = []
    for seq_path in seq_files[:20]:
        meta = read_json(seq_path).get("meta", {})
        preview.append(
            {
                "sequence_file": str(seq_path),
                "bot": bot_folder_for_sequence(data_dir, seq_path),
                "difficulty": difficulty_from_meta(meta, seq_path),
                "enemy_race": meta.get("enemy_race"),
                "map": meta.get("map"),
                "skip_existing": skip_existing and md_is_valid(_expected_md_path(md_dir, data_dir, seq_path)),
            }
        )
    write_json(
        output_dir / "sequence_order_preview.json",
        {
            "mode": sequence_order,
            "total": len(seq_files),
            "first_20": preview,
        },
    )
    print(f"Sequence order: {sequence_order} ({len(seq_files)} trajectories)")
    for i, item in enumerate(preview[:10], start=1):
        print(
            f"  {i:02d}. {item['bot']} | {item['difficulty']} | "
            f"{item['enemy_race']} | {item['map']}"
            + (" | skip" if item["skip_existing"] else "")
        )


def build_v7_steps(
    data_dir: Path,
    output_dir: Path,
    limit: int | None = None,
    model_key: str = "deepseek-v4-flash",
    require_victory: bool = True,
    workers: int = 1,
    sequence_order: str = "diverse-hard-first",
    skip_existing: bool = False,
) -> dict[str, Any]:
    md_dir = output_dir / "md"
    json_dir = output_dir / "json"
    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    labeled_rows: list[dict[str, Any]] = []
    step_index: dict[str, Any] = {}

    original_call_llm = bo_to_doc_v7._call_llm

    def _call_with_model(messages: list[dict[str, str]], model_key: str = model_key) -> str:
        return original_call_llm(messages, model_key=model_key)

    bo_to_doc_v7._call_llm = _call_with_model

    try:
        seq_files = victory_sequence_files(list(iter_sequence_files(data_dir)), require_victory)
        seq_files = order_sequences(seq_files, data_dir, sequence_order)
        if limit is not None:
            seq_files = seq_files[:limit]

        if sequence_order != "default":
            _write_sequence_order_preview(output_dir, data_dir, md_dir, seq_files, sequence_order, skip_existing)

        def _process_sequence(seq_path: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
            seq_data = read_json(seq_path)
            meta = seq_data.get("meta", {})
            if require_victory and meta.get("result") != "Victory":
                return None
            order_list = list(seq_data.get("order_list") or [])
            sequence = list(seq_data.get("sequence") or [])
            if not order_list or not sequence:
                return None

            bot_folder = bot_folder_for_sequence(data_dir, seq_path)
            sample_key = _sample_key(bot_folder, seq_path, meta)
            md_path = _expected_md_path(md_dir, data_dir, seq_path)
            if skip_existing and md_is_valid(md_path):
                print(f"  [{sample_key}] skip existing md")
                return _rows_from_existing_md(data_dir, seq_path, md_path)
            if md_path.exists() and not md_is_valid(md_path):
                print(f"  [{sample_key}] invalid existing md detected, re-labeling")

            mapper = ActionMapper()
            try:
                result = process_trajectory(
                    sample_key,
                    str(seq_path),
                    meta,
                    order_list,
                    mapper,
                    str(md_dir),
                    model_key,
                )
            except Exception as exc:
                print(f"  [{sample_key}] FAILED: {exc}")
                if md_path.exists() and not md_is_valid(md_path):
                    md_path.unlink(missing_ok=True)
                return None
            md_path = Path(result["md_path"]).resolve()
            md_steps = _parse_md_steps(md_path)

            normal_steps: list[dict[str, Any]] = []
            rows: list[dict[str, Any]] = []
            for step in result.get("steps", []):
                if step.get("is_final_step"):
                    continue
                action_range = step.get("range")
                if not action_range:
                    continue
                start, end = int(action_range[0]), int(action_range[1])
                if start < 0 or end >= len(sequence):
                    continue
                step_id = int(step["step"])
                row = {
                    "sample_id": f"{sample_key}/step_{step_id:03d}",
                    "source_sequence_file": str(seq_path.resolve()),
                    "md_path": str(md_path),
                    "bot": bot_folder,
                    "bot_name": meta.get("bot_name"),
                    "map": meta.get("map"),
                    "enemy_race": meta.get("enemy_race"),
                    "opponent_id": meta.get("opponent_id"),
                    "result": meta.get("result"),
                    "step_id": step_id,
                    "action_range": [start, end],
                    "ordered_actions": order_list[start : end + 1],
                    "step_text_v7": md_steps.get(step_id, ""),
                    "step_text_v6": md_steps.get(step_id, ""),
                    "obs_at_step_start": sequence[start].get("obs", {}),
                    "obs_at_each_action": [
                        {
                            "seq": entry.get("seq"),
                            "ability": entry.get("ability"),
                            "obs": entry.get("obs", {}),
                            "local_obs": entry.get("local_obs", {}),
                            "executor_context": entry.get("executor_context"),
                        }
                        for entry in sequence[start : end + 1]
                    ],
                }
                rows.append(row)
                normal_steps.append(
                    {
                        "step": step_id,
                        "range": [start, end],
                        "action_count": end - start + 1,
                        "step_text_v7": row["step_text_v7"],
                        "step_text_v6": row["step_text_v6"],
                    }
                )

            index_entry = {
                "source_sequence_file": str(seq_path.resolve()),
                "md_path": str(md_path),
                "total_actions": len(order_list),
                "total_steps": len(normal_steps),
                "steps": normal_steps,
            }
            return sample_key, index_entry, rows

        if workers <= 1:
            for seq_path in seq_files:
                item = _process_sequence(seq_path)
                if item is None:
                    continue
                sample_key, index_entry, rows = item
                step_index[sample_key] = index_entry
                labeled_rows.extend(rows)
        else:
            results = run_ordered_pool(seq_files, workers, _process_sequence)
            for sample_key, index_entry, rows in sorted(results, key=lambda item: item[0]):
                step_index[sample_key] = index_entry
                labeled_rows.extend(rows)
    finally:
        bo_to_doc_v7._call_llm = original_call_llm

    labeled_path = json_dir / "labeled_steps.jsonl"
    write_jsonl(labeled_path, labeled_rows)
    index_path = json_dir / "step_index.json"
    write_json(index_path, {"items": step_index, "total_steps": len(labeled_rows)})
    manifest = {
        "created_at": datetime.now().isoformat(),
        "standard": "v7",
        "model_key": model_key,
        "require_victory": require_victory,
        "workers": workers,
        "sequence_order": sequence_order,
        "skip_existing": skip_existing,
        "data_dir": str(data_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "sequence_count": len(seq_files),
        "labeled_step_count": len(labeled_rows),
        "md_dir": str(md_dir.resolve()),
        "labeled_steps": str(labeled_path.resolve()),
        "step_index": str(index_path.resolve()),
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build v7 BO markdown docs plus machine-readable step JSONL.")
    parser.add_argument("--data-dir", required=True, help="Collection run root or a sequence JSON file.")
    parser.add_argument("--output", required=True, help="Output directory for v7 md/json artifacts.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of sequence files.")
    parser.add_argument("--workers", type=int, default=1, help="Maximum concurrent trajectory labeling workers.")
    parser.add_argument("--model-key", default="deepseek-v4-flash", help="bo_2_nlstep API model_key.")
    parser.add_argument("--no-thinking", action="store_true", help="Documentation flag; use a non-thinking model_key config.")
    parser.add_argument(
        "--sequence-order",
        default="diverse-hard-first",
        choices=["default", "diverse-hard-first"],
        help="Trajectory processing order. diverse-hard-first round-robins bots, prefers harder wins, and balances enemy races.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip trajectories whose output markdown already exists and has no failed steps.",
    )
    parser.add_argument(
        "--include-non-victory",
        action="store_true",
        help="Include non-Victory trajectories. Default is to keep only wins.",
    )
    args = parser.parse_args()

    manifest = build_v7_steps(
        Path(args.data_dir),
        Path(args.output),
        args.limit,
        args.model_key,
        require_victory=not args.include_non_victory,
        workers=args.workers,
        sequence_order=args.sequence_order,
        skip_existing=args.skip_existing,
    )
    print(f"Wrote {manifest['labeled_step_count']} labeled steps")
    print(f"Markdown: {manifest['md_dir']}")
    print(f"JSONL:    {manifest['labeled_steps']}")


if __name__ == "__main__":
    main()
