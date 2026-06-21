from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from bo_to_doc_v6 import process_trajectory  # type: ignore  # noqa: E402


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


def build_v6_steps(
    data_dir: Path,
    output_dir: Path,
    limit: int | None = None,
    model_key: str = "deepseek-v4-flash",
    require_victory: bool = True,
    workers: int = 1,
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
        seq_files = list(iter_sequence_files(data_dir))
        if limit is not None:
            seq_files = seq_files[:limit]

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
            mapper = ActionMapper()
            result = process_trajectory(
                sample_key,
                str(seq_path),
                meta,
                order_list,
                mapper,
                str(md_dir),
            )
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
            with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                futures = {executor.submit(_process_sequence, seq_path): seq_path for seq_path in seq_files}
                results: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
                for future in as_completed(futures):
                    item = future.result()
                    if item is not None:
                        results.append(item)
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
    )
    print(f"Wrote {manifest['labeled_step_count']} labeled steps")
    print(f"Markdown: {manifest['md_dir']}")
    print(f"JSONL:    {manifest['labeled_steps']}")


if __name__ == "__main__":
    main()
