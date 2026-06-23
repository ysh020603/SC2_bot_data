from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import iter_sequence_files, read_json, safe_stem, write_json, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
BO_TO_NL_TOOLS = ROOT / "bo_2_nlstep" / "Tools"
if str(BO_TO_NL_TOOLS) not in sys.path:
    sys.path.insert(0, str(BO_TO_NL_TOOLS))

from action_mapper import ActionMapper  # type: ignore  # noqa: E402
import bo_to_doc_v6  # type: ignore  # noqa: E402
from bo_to_doc_v6 import process_trajectory, split_into_steps  # type: ignore  # noqa: E402


STEP_RE = re.compile(r"^\[Step\s+(?P<num>\d+)\]\s*(?P<text>.*)$")
LLM_FAILED_MARK = "*(LLM call failed)*"

DIFFICULTY_RANK = {
    "veryhard": 0,
    "harder": 1,
    "hard": 2,
    "mediumhard": 3,
    "medium": 4,
}


def _difficulty_from_meta(meta: dict[str, Any], seq_path: Path) -> str:
    difficulty = str(meta.get("difficulty") or "").strip().lower()
    if difficulty:
        return difficulty
    opponent_id = str(meta.get("opponent_id") or seq_path.stem)
    parts = opponent_id.split(".")
    if parts:
        tail = parts[-1].lower()
        if tail in DIFFICULTY_RANK:
            return tail
    for token in reversed(seq_path.stem.replace(" ", "_").split("_")):
        if token.lower() in DIFFICULTY_RANK:
            return token.lower()
    return "unknown"


def _difficulty_sort_key(meta: dict[str, Any], seq_path: Path) -> tuple[int, str]:
    difficulty = _difficulty_from_meta(meta, seq_path)
    return (DIFFICULTY_RANK.get(difficulty, 99), difficulty)


def order_sequences(
    seq_files: list[Path],
    data_dir: Path,
    mode: str,
) -> list[Path]:
    if mode == "default":
        return seq_files

    if mode != "diverse-hard-first":
        raise ValueError(f"Unsupported sequence order mode: {mode}")

    grouped: dict[str, list[Path]] = defaultdict(list)
    for seq_path in seq_files:
        bot_folder = _bot_folder_for_sequence(data_dir, seq_path)
        grouped[bot_folder].append(seq_path)

    for bot_folder in grouped:
        grouped[bot_folder].sort(
            key=lambda path: _difficulty_sort_key(read_json(path).get("meta", {}), path)
        )

    ordered: list[Path] = []
    bot_names = sorted(grouped)
    max_len = max((len(items) for items in grouped.values()), default=0)
    for round_idx in range(max_len):
        for bot_folder in bot_names:
            items = grouped[bot_folder]
            if round_idx < len(items):
                ordered.append(items[round_idx])
    return ordered


def _md_is_valid(md_path: Path) -> bool:
    if not md_path.exists():
        return False
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return LLM_FAILED_MARK not in text


def _expected_md_path(md_dir: Path, data_dir: Path, seq_path: Path) -> Path:
    meta = read_json(seq_path).get("meta", {})
    bot_folder = _bot_folder_for_sequence(data_dir, seq_path)
    sample_key = _sample_key(bot_folder, seq_path, meta)
    return md_dir / f"{sample_key}.md"


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


def _bot_folder_for_sequence(data_dir: Path, seq_path: Path) -> str:
    try:
        rel = seq_path.relative_to(data_dir)
        parts = rel.parts
        if len(parts) >= 3 and parts[-2] == "sequences":
            return parts[-3]
        if len(parts) >= 2:
            return parts[0]
    except ValueError:
        pass
    return seq_path.parent.parent.name if seq_path.parent.name == "sequences" else seq_path.parent.name


def _sample_key(bot_folder: str, seq_path: Path, meta: dict[str, Any]) -> str:
    opponent = str(meta.get("opponent_id") or "unknown")
    map_name = str(meta.get("map") or meta.get("map_engine") or "unknown_map")
    recorded_at = str(meta.get("recorded_at") or seq_path.stem)
    return safe_stem(f"{bot_folder}_{opponent}_{map_name}_{recorded_at}")


def _victory_sequence_files(
    seq_files: list[Path],
    require_victory: bool,
) -> list[Path]:
    if not require_victory:
        return seq_files
    victory_files: list[Path] = []
    for seq_path in seq_files:
        seq_data = read_json(seq_path)
        meta = seq_data.get("meta", {})
        if meta.get("result") != "Victory":
            continue
        if not seq_data.get("order_list") or not seq_data.get("sequence"):
            continue
        victory_files.append(seq_path)
    return victory_files


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

    bot_folder = _bot_folder_for_sequence(data_dir, seq_path)
    sample_key = _sample_key(bot_folder, seq_path, meta)
    md_steps = _parse_md_steps(md_path)
    step_ranges = split_into_steps(order_list)

    normal_steps: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for idx, (start, end) in enumerate(step_ranges, start=1):
        step_text = md_steps.get(idx, "")
        if not step_text:
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


def _run_ordered_pool(
    seq_files: list[Path],
    workers: int,
    process_fn,
) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    results: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    next_idx = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        pending: dict[Any, Path] = {}
        while next_idx < len(seq_files) or pending:
            while next_idx < len(seq_files) and len(pending) < workers:
                seq_path = seq_files[next_idx]
                pending[executor.submit(process_fn, seq_path)] = seq_path
                next_idx += 1
            if not pending:
                break
            done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                item = future.result()
                if item is not None:
                    results.append(item)
                del pending[future]
    return results


def build_v6_steps(
    data_dir: Path,
    output_dir: Path,
    limit: int | None = None,
    model_key: str = "deepseek-v4-flash",
    require_victory: bool = True,
    workers: int = 1,
    sequence_order: str = "default",
    skip_existing: bool = False,
) -> dict[str, Any]:
    md_dir = output_dir / "md"
    json_dir = output_dir / "json"
    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    labeled_rows: list[dict[str, Any]] = []
    step_index: dict[str, Any] = {}

    original_call_llm = bo_to_doc_v6._call_llm

    def _call_with_model(messages: list[dict[str, str]], model_key: str = model_key) -> str:
        return original_call_llm(messages, model_key=model_key)

    bo_to_doc_v6._call_llm = _call_with_model

    try:
        seq_files = _victory_sequence_files(list(iter_sequence_files(data_dir)), require_victory)
        seq_files = order_sequences(seq_files, data_dir, sequence_order)
        if limit is not None:
            seq_files = seq_files[:limit]

        if sequence_order != "default":
            preview = []
            for seq_path in seq_files[:20]:
                meta = read_json(seq_path).get("meta", {})
                preview.append(
                    {
                        "sequence_file": str(seq_path),
                        "bot": _bot_folder_for_sequence(data_dir, seq_path),
                        "difficulty": _difficulty_from_meta(meta, seq_path),
                        "enemy_race": meta.get("enemy_race"),
                        "map": meta.get("map"),
                        "skip_existing": skip_existing
                        and _md_is_valid(_expected_md_path(md_dir, data_dir, seq_path)),
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

        def _process_sequence(seq_path: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
            seq_data = read_json(seq_path)
            meta = seq_data.get("meta", {})
            if require_victory and meta.get("result") != "Victory":
                return None
            order_list = list(seq_data.get("order_list") or [])
            sequence = list(seq_data.get("sequence") or [])
            if not order_list or not sequence:
                return None

            bot_folder = _bot_folder_for_sequence(data_dir, seq_path)
            sample_key = _sample_key(bot_folder, seq_path, meta)
            md_path = _expected_md_path(md_dir, data_dir, seq_path)
            if skip_existing and _md_is_valid(md_path):
                print(f"  [{sample_key}] skip existing md")
                return _rows_from_existing_md(data_dir, seq_path, md_path)
            if md_path.exists() and not _md_is_valid(md_path):
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
                )
            except Exception as exc:
                print(f"  [{sample_key}] FAILED: {exc}")
                if md_path.exists() and not _md_is_valid(md_path):
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
            results = _run_ordered_pool(seq_files, workers, _process_sequence)
            for sample_key, index_entry, rows in sorted(results, key=lambda item: item[0]):
                step_index[sample_key] = index_entry
                labeled_rows.extend(rows)
    finally:
        bo_to_doc_v6._call_llm = original_call_llm

    labeled_path = json_dir / "labeled_steps.jsonl"
    write_jsonl(labeled_path, labeled_rows)
    index_path = json_dir / "step_index.json"
    write_json(index_path, {"items": step_index, "total_steps": len(labeled_rows)})
    manifest = {
        "created_at": datetime.now().isoformat(),
        "standard": "v6",
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
    parser = argparse.ArgumentParser(description="Build v6 BO markdown docs plus machine-readable step JSONL.")
    parser.add_argument("--data-dir", required=True, help="Collection run root or a sequence JSON file.")
    parser.add_argument("--output", required=True, help="Output directory for v6 md/json artifacts.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of sequence files.")
    parser.add_argument("--workers", type=int, default=1, help="Maximum concurrent trajectory labeling workers.")
    parser.add_argument("--model-key", default="deepseek-v4-flash", help="bo_2_nlstep API model_key.")
    parser.add_argument("--no-thinking", action="store_true", help="Documentation flag; use a non-thinking model_key config.")
    parser.add_argument(
        "--sequence-order",
        default="default",
        choices=["default", "diverse-hard-first"],
        help="Trajectory processing order. diverse-hard-first round-robins bots and prefers harder wins.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip trajectories whose output markdown already exists in the output md/ directory.",
    )
    parser.add_argument(
        "--include-non-victory",
        action="store_true",
        help="Include non-Victory trajectories. Default is to keep only wins.",
    )
    args = parser.parse_args()

    manifest = build_v6_steps(
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
