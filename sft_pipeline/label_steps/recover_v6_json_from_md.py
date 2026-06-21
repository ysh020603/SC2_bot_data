from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sft_pipeline.common.io import iter_sequence_files, read_json, safe_stem, write_json, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
BO_TO_NL_TOOLS = ROOT / "bo_2_nlstep" / "Tools"
if str(BO_TO_NL_TOOLS) not in sys.path:
    sys.path.insert(0, str(BO_TO_NL_TOOLS))

from bo_to_doc_v6 import split_into_steps  # type: ignore  # noqa: E402


STEP_RE = re.compile(r"^\[Step\s+(?P<num>\d+)\]\s*(?P<text>.*)$")


def _parse_md_steps(md_path: Path) -> dict[int, str]:
    steps: dict[int, str] = {}
    with md_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            match = STEP_RE.match(line)
            if match:
                steps[int(match.group("num"))] = line
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


def _legacy_sample_key(bot_folder: str, seq_path: Path) -> str:
    return safe_stem(f"{bot_folder}_{seq_path.stem}")


def recover_v6_json_from_md(
    data_dir: Path,
    md_dir: Path,
    output_dir: Path,
    require_victory: bool = True,
) -> dict[str, Any]:
    json_dir = output_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    labeled_rows: list[dict[str, Any]] = []
    step_index: dict[str, Any] = {}
    qa = {
        "sequence_count": 0,
        "victory_sequences": 0,
        "matched_md_sequences": 0,
        "skipped_non_victory": 0,
        "skipped_missing_md": 0,
        "skipped_missing_step_text": 0,
    }

    for seq_path in iter_sequence_files(data_dir):
        qa["sequence_count"] += 1
        seq_data = read_json(seq_path)
        meta = seq_data.get("meta", {})
        if require_victory and meta.get("result") != "Victory":
            qa["skipped_non_victory"] += 1
            continue
        if meta.get("result") == "Victory":
            qa["victory_sequences"] += 1

        order_list = list(seq_data.get("order_list") or [])
        sequence = list(seq_data.get("sequence") or [])
        if not order_list or not sequence:
            continue

        bot_folder = _bot_folder_for_sequence(data_dir, seq_path)
        sample_key = _sample_key(bot_folder, seq_path, meta)
        md_path = md_dir / f"{sample_key}.md"
        if not md_path.exists():
            legacy_key = _legacy_sample_key(bot_folder, seq_path)
            legacy_md_path = md_dir / f"{legacy_key}.md"
            if legacy_md_path.exists():
                sample_key = legacy_key
                md_path = legacy_md_path
            else:
                qa["skipped_missing_md"] += 1
                continue

        qa["matched_md_sequences"] += 1
        md_steps = _parse_md_steps(md_path)
        step_ranges = split_into_steps(order_list)
        normal_steps: list[dict[str, Any]] = []

        for idx, (start, end) in enumerate(step_ranges, start=1):
            step_text = md_steps.get(idx, "")
            if not step_text:
                qa["skipped_missing_step_text"] += 1
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
            labeled_rows.append(row)
            normal_steps.append(
                {
                    "step": idx,
                    "range": [start, end],
                    "action_count": end - start + 1,
                    "step_text_v6": step_text,
                }
            )

        step_index[sample_key] = {
            "source_sequence_file": str(seq_path.resolve()),
            "md_path": str(md_path.resolve()),
            "total_actions": len(order_list),
            "total_steps": len(normal_steps),
            "steps": normal_steps,
        }

    labeled_path = json_dir / "labeled_steps.jsonl"
    write_jsonl(labeled_path, labeled_rows)
    index_path = json_dir / "step_index.json"
    write_json(index_path, {"items": step_index, "total_steps": len(labeled_rows)})

    manifest = {
        "created_at": datetime.now().isoformat(),
        "standard": "v6",
        "source": "recovered_from_existing_markdown",
        "require_victory": require_victory,
        "data_dir": str(data_dir.resolve()),
        "md_dir": str(md_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "labeled_step_count": len(labeled_rows),
        "labeled_steps": str(labeled_path.resolve()),
        "step_index": str(index_path.resolve()),
        "qa": qa,
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover v6 step JSONL from existing MD without LLM calls.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--md-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--include-non-victory",
        action="store_true",
        help="Include non-Victory trajectories. Default keeps only wins.",
    )
    args = parser.parse_args()

    manifest = recover_v6_json_from_md(
        Path(args.data_dir),
        Path(args.md_dir),
        Path(args.output),
        require_victory=not args.include_non_victory,
    )
    print(f"Wrote {manifest['labeled_step_count']} recovered labeled steps")
    print(f"JSONL: {manifest['labeled_steps']}")
    print(f"QA: {manifest['qa']}")


if __name__ == "__main__":
    main()
