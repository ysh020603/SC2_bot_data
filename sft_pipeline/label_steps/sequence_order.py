from __future__ import annotations

from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from sft_pipeline.common.io import read_json

DIFFICULTY_RANK = {
    "veryhard": 0,
    "harder": 1,
    "hard": 2,
    "mediumhard": 3,
    "medium": 4,
}

ENEMY_RACES = frozenset({"zerg", "protoss", "terran"})

LLM_FAILED_MARK = "*(LLM call failed)*"


def bot_folder_for_sequence(data_dir: Path, seq_path: Path) -> str:
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


def difficulty_from_meta(meta: dict[str, Any], seq_path: Path) -> str:
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


def enemy_race_from_meta(meta: dict[str, Any], seq_path: Path) -> str:
    race = str(meta.get("enemy_race") or "").strip().lower()
    if race in ENEMY_RACES:
        return race
    opponent_id = str(meta.get("opponent_id") or seq_path.stem)
    parts = opponent_id.split(".")
    if len(parts) >= 2:
        candidate = parts[1].lower()
        if candidate in ENEMY_RACES:
            return candidate
    return "unknown"


def difficulty_sort_key(meta: dict[str, Any], seq_path: Path) -> tuple[int, str]:
    difficulty = difficulty_from_meta(meta, seq_path)
    return (DIFFICULTY_RANK.get(difficulty, 99), difficulty)


def _pick_next_for_bot(
    items: list[Path],
    meta_cache: dict[Path, dict[str, Any]],
    race_counts: dict[str, int],
) -> Path:
    best_diff = min(difficulty_sort_key(meta_cache[path], path) for path in items)
    candidates = [
        path for path in items if difficulty_sort_key(meta_cache[path], path) == best_diff
    ]

    def _candidate_key(path: Path) -> tuple[int, str]:
        race = enemy_race_from_meta(meta_cache[path], path)
        return (race_counts.get(race, 0), str(path))

    return min(candidates, key=_candidate_key)


def order_sequences(
    seq_files: list[Path],
    data_dir: Path,
    mode: str,
) -> list[Path]:
    if mode == "default":
        return seq_files

    if mode != "diverse-hard-first":
        raise ValueError(f"Unsupported sequence order mode: {mode}")

    meta_cache = {seq_path: read_json(seq_path).get("meta", {}) for seq_path in seq_files}

    grouped: dict[str, list[Path]] = defaultdict(list)
    for seq_path in seq_files:
        grouped[bot_folder_for_sequence(data_dir, seq_path)].append(seq_path)

    remaining = {bot_folder: list(paths) for bot_folder, paths in grouped.items()}
    ordered: list[Path] = []
    race_counts: dict[str, int] = defaultdict(int)

    while any(remaining.values()):
        for bot_folder in sorted(remaining):
            items = remaining[bot_folder]
            if not items:
                continue
            pick = _pick_next_for_bot(items, meta_cache, race_counts)
            items.remove(pick)
            ordered.append(pick)
            race = enemy_race_from_meta(meta_cache[pick], pick)
            race_counts[race] += 1

    return ordered


def victory_sequence_files(
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


def md_is_valid(md_path: Path) -> bool:
    if not md_path.exists():
        return False
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return LLM_FAILED_MARK not in text


def run_ordered_pool(
    seq_files: list[Path],
    workers: int,
    process_fn: Callable[[Path], Any],
) -> list[Any]:
    results: list[Any] = []
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
