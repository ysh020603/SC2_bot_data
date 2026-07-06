#!/usr/bin/env python3
"""Batch-collect ability-sequence BO data for Terran dummy bots.

Runs each bot against all three races at medium/mediumhard/hard/harder/veryhard AI difficulty.
Within each bot, all matchups are launched in parallel; bots are processed sequentially unless
--parallel-bots is set, in which case all tasks across bots run in one shared pool.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from configparser import ConfigParser
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Project root on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "python-sc2"))
os.chdir(ROOT)

from bot_loader.bot_definitions import BotDefinitions  # noqa: E402
from bot_loader.game_starter import GameStarter, known_melee_maps  # noqa: E402
from bot_loader.runner import MatchRunner  # noqa: E402
from config import get_config  # noqa: E402
from sc2 import maps  # noqa: E402
from sc2.data import Result  # noqa: E402
from sc2.player import Bot  # noqa: E402
from sharpy.knowledges import KnowledgeBot  # noqa: E402
from sharpy.tools import LoggingUtility  # noqa: E402

# bot_loader key -> output folder name (from source file stem)
TERRAN_BOTS: List[Tuple[str, str]] = [
    ("banshee", "banshees"),
    ("bc", "battle_cruisers"),
    ("bio", "bio"),
    ("cyclone", "cyclones"),
    ("marine", "marine_rush"),
    ("terranturtle", "one_base_turtle"),
    ("oldrusty", "rusty"),
    ("saferaven", "safe_tvt_raven"),
    ("silverbio", "terran_silver_bio"),
    ("tank", "two_base_tanks"),
    ("threerax", "three_rax_stim"),
    ("safe211", "safe_211_mine"),
    ("biomine", "bio_mine_macro"),
    ("ravlibtank", "raven_liberator_tank"),
    ("mechthor", "tank_thor_mech"),
    ("ravenscreams", "raven_screams"),
    ("yamatofleet", "yamato_rust_fleet"),
    ("biominesv2", "rusty_bio_mines"),
    ("blueflame", "blueflame_locks"),
    ("stimrush", "stim_rush_relay"),
    ("rustyanvil", "old_rusty_anvil"),
    ("matrixtank", "two_base_matrix_tanks"),
]

RACES = ("protoss", "zerg", "terran")
DEFAULT_DIFFICULTIES = ("medium", "mediumhard", "hard", "harder", "veryhard")
BASE_PORT = 25000
PORT_STRIDE = 8


def _make_config(
    sequence_dir: str,
    log_file: bool,
    map_name: str | None = None,
    sequence_path: str | None = None,
    match_id: str | None = None,
) -> ConfigParser:
    config = deepcopy(get_config())
    config["general"]["write_ability_sequence"] = "yes"
    config["general"]["ability_sequence_dir"] = sequence_dir
    config["general"]["log_file"] = "yes" if log_file else "no"
    if map_name:
        config["general"]["ability_sequence_map_name"] = map_name
    if sequence_path:
        config["general"]["ability_sequence_path"] = sequence_path
    if match_id:
        config["general"]["ability_sequence_match_id"] = match_id
    return config


def _pick_map(available: List[str]) -> str:
    candidates = [m for m in known_melee_maps if m in available]
    if not candidates:
        raise RuntimeError("No known melee maps installed.")
    return random.choice(candidates)


def run_match(task: Dict[str, Any]) -> Dict[str, Any]:
    """Worker entry: play one game and return result metadata."""
    bot_key: str = task["bot_key"]
    bot_folder: str = task["bot_folder"]
    enemy_race: str = task["enemy_race"]
    difficulty: str = task["difficulty"]
    map_name: str = task["map_name"]
    port: int = task["port"]
    output_root: str = task["output_root"]
    repeat_index: int = task.get("repeat_index", 1)

    bot_dir = os.path.join(output_root, bot_folder)
    seq_dir = os.path.join(bot_dir, "sequences")
    log_dir = os.path.join(bot_dir, "logs")
    replay_dir = os.path.join(bot_dir, "replays")
    for folder in (seq_dir, log_dir, replay_dir):
        os.makedirs(folder, exist_ok=True)

    enemy_build: str = task.get("enemy_build", "random")
    player1_id = bot_key
    if enemy_build and enemy_build != "random":
        player2_id = f"ai.{enemy_race}.{difficulty}.{enemy_build}"
    else:
        player2_id = f"ai.{enemy_race}.{difficulty}"
    stamp = datetime.now().strftime("%Y-%m-%d %H_%M_%S")
    tag = random.randint(0, 999999)
    base_name = f"{bot_key}-{player2_id}_{map_name}_{stamp}_{tag}"
    log_path = os.path.join(log_dir, f"{base_name}.log")
    replay_path = os.path.join(replay_dir, f"{base_name}.SC2Replay")
    sequence_path = os.path.join(seq_dir, f"{base_name}.json")

    result_record: Dict[str, Any] = {
        "bot_key": bot_key,
        "bot_folder": bot_folder,
        "opponent": player2_id,
        "enemy_race": enemy_race,
        "difficulty": difficulty,
        "map": map_name,
        "repeat_index": repeat_index,
        "port": port,
        "log_path": log_path,
        "replay_path": replay_path,
        "sequence_dir": seq_dir,
        "status": "error",
        "result": None,
        "error": None,
        "match_id": base_name,
        "sequence_file": sequence_path,
    }

    try:
        definitions = BotDefinitions()
        playable = definitions.playable

        config = _make_config(
            seq_dir,
            log_file=True,
            map_name=map_name,
            sequence_path=sequence_path,
            match_id=base_name,
        )
        LoggingUtility.set_logger_file(log_level=config["general"]["log_level"], path=log_path)

        player1_bot = playable[bot_key]([])
        ai_params = [enemy_race, difficulty]
        if enemy_build and enemy_build != "random":
            ai_params.append(enemy_build)
        player2_bot = playable["ai"](ai_params)

        GameStarter.setup_bot(player1_bot, player1_id, player2_id, argparse.Namespace(raw_selection=False, release=False))
        GameStarter.setup_bot(player2_bot, player2_id, player1_id, argparse.Namespace(raw_selection=False, release=False))

        if isinstance(player1_bot, Bot) and hasattr(player1_bot.ai, "config"):
            my_bot: KnowledgeBot = player1_bot.ai
            my_bot.config = config
            setattr(my_bot, "ability_sequence_map_name", map_name)

        runner = MatchRunner()
        game_result = runner.run_game(
            maps.get(map_name),
            [player1_bot, player2_bot],
            player1_id=player1_id,
            realtime=False,
            game_time_limit=(20 * 60),
            save_replay_as=replay_path,
            start_port=str(port),
        )

        if not os.path.isfile(sequence_path):
            raise RuntimeError(f"Recorder did not create expected sequence file: {sequence_path}")
        with open(sequence_path, "r", encoding="utf-8") as handle:
            sequence_payload = json.load(handle)
        recorded_match_id = sequence_payload.get("meta", {}).get("match_id")
        if recorded_match_id != base_name:
            raise RuntimeError(
                f"Sequence identity mismatch: expected {base_name!r}, got {recorded_match_id!r}"
            )

        result_record["status"] = "ok"
        result_record["result"] = game_result.name if isinstance(game_result, Result) else str(game_result)
        result_record["victory"] = game_result == Result.Victory
    except Exception as exc:
        result_record["error"] = f"{type(exc).__name__}: {exc}"
        result_record["traceback"] = traceback.format_exc()
    finally:
        try:
            import sc2.main

            sc2.main.logger.remove()
        except Exception:
            pass

    return result_record


def build_tasks(
    output_root: str,
    bot_filter: Optional[List[str]],
    map_name: Optional[str],
    port_offset: int,
    difficulties: Optional[List[str]] = None,
    enemy_build: str = "random",
    repeats: int = 1,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    definitions = BotDefinitions()
    available_maps = GameStarter.installed_maps()
    chosen_map = map_name or _pick_map(available_maps)
    if chosen_map not in available_maps:
        raise ValueError(f"Map {chosen_map!r} not installed. Available: {available_maps[:5]}...")

    bots = TERRAN_BOTS
    if bot_filter:
        allowed = set(bot_filter)
        bots = [(k, f) for k, f in TERRAN_BOTS if k in allowed or f in allowed]

    diff_list = tuple(difficulties) if difficulties else DEFAULT_DIFFICULTIES
    if repeats < 1:
        raise ValueError("repeats must be at least 1")

    tasks: List[Dict[str, Any]] = []
    port_idx = 0
    for bot_key, bot_folder in bots:
        for enemy_race in RACES:
            for difficulty in diff_list:
                for repeat_index in range(1, repeats + 1):
                    tasks.append(
                        {
                            "bot_key": bot_key,
                            "bot_folder": bot_folder,
                            "enemy_race": enemy_race,
                            "difficulty": difficulty,
                            "enemy_build": enemy_build,
                            "repeat_index": repeat_index,
                            "map_name": chosen_map,
                            "port": port_offset + port_idx * PORT_STRIDE,
                            "output_root": output_root,
                        }
                    )
                    port_idx += 1

    return tasks, [chosen_map]


def group_tasks_by_bot(tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for task in tasks:
        groups.setdefault(task["bot_folder"], []).append(task)
    return [groups[folder] for _, folder in TERRAN_BOTS if folder in groups]


def _opponent_id(enemy_race: str, difficulty: str, enemy_build: str = "random") -> str:
    if enemy_build and enemy_build != "random":
        return f"ai.{enemy_race}.{difficulty}.{enemy_build}"
    return f"ai.{enemy_race}.{difficulty}"


def _matchup_file_prefix(task: Dict[str, Any]) -> str:
    enemy_build = task.get("enemy_build", "random")
    return (
        f"{task['bot_key']}-{_opponent_id(task['enemy_race'], task['difficulty'], enemy_build)}"
        f"_{task['map_name']}"
    )


def _result_task_key(record: Dict[str, Any]) -> Tuple[str, str, int]:
    return (
        record.get("enemy_race", ""),
        record.get("difficulty", ""),
        record.get("repeat_index", 1),
    )


def _is_valid_sequence_file(path: str, task: Dict[str, Any]) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        meta = payload.get("meta", {})
        if not meta.get("match_id"):
            return False
        expected_opponent = (
            f"{task['bot_key']}-{_opponent_id(task['enemy_race'], task['difficulty'], task.get('enemy_build', 'random'))}"
        )
        opponent_id = meta.get("opponent_id", "")
        return opponent_id == expected_opponent and meta.get("map") == task["map_name"]
    except (OSError, json.JSONDecodeError):
        return False


def _is_task_complete(task: Dict[str, Any]) -> bool:
    bot_dir = os.path.join(task["output_root"], task["bot_folder"])
    seq_dir = os.path.join(bot_dir, "sequences")
    prefix = _matchup_file_prefix(task)
    if not os.path.isdir(seq_dir):
        return False
    for name in os.listdir(seq_dir):
        if not name.startswith(prefix) or not name.endswith(".json"):
            continue
        if _is_valid_sequence_file(os.path.join(seq_dir, name), task):
            return True
    return False


def _cleanup_stale_artifacts(task: Dict[str, Any]) -> None:
    bot_dir = os.path.join(task["output_root"], task["bot_folder"])
    prefix = _matchup_file_prefix(task)
    for sub, suffix in (("logs", ".log"), ("replays", ".SC2Replay")):
        dirpath = os.path.join(bot_dir, sub)
        if not os.path.isdir(dirpath):
            continue
        for name in os.listdir(dirpath):
            if name.startswith(prefix) and name.endswith(suffix):
                try:
                    os.remove(os.path.join(dirpath, name))
                except OSError:
                    pass


def _load_bot_results(bot_dir: str) -> List[Dict[str, Any]]:
    path = os.path.join(bot_dir, "results.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("matches", [])


def _merge_bot_results(existing: List[Dict[str, Any]], new_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for record in existing:
        key = _result_task_key(record)
        seq = record.get("sequence_file")
        if record.get("status") == "ok" and seq and os.path.isfile(seq):
            merged[key] = record
    for record in new_results:
        merged[_result_task_key(record)] = record
    return list(merged.values())


def filter_pending_tasks(
    tasks: List[Dict[str, Any]],
    skip_existing: bool,
    cleanup_stale: bool,
) -> List[Dict[str, Any]]:
    pending: List[Dict[str, Any]] = []
    for task in tasks:
        if skip_existing and _is_task_complete(task):
            continue
        if cleanup_stale:
            _cleanup_stale_artifacts(task)
        pending.append(task)
    return pending


def save_summary(output_root: str, all_results: List[Dict[str, Any]]) -> str:
    summary_path = os.path.join(output_root, "summary.json")
    wins = sum(1 for r in all_results if r.get("victory"))
    payload = {
        "recorded_at": datetime.now().isoformat(),
        "total_games": len(all_results),
        "wins": wins,
        "losses": len(all_results) - wins,
        "results": all_results,
    }
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return summary_path


def save_bot_summary(bot_dir: str, results: List[Dict[str, Any]]) -> None:
    path = os.path.join(bot_dir, "results.json")
    wins = sum(1 for r in results if r.get("victory"))
    payload = {
        "recorded_at": datetime.now().isoformat(),
        "total_games": len(results),
        "wins": wins,
        "losses": len(results) - wins,
        "matches": results,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def run_task_batch(
    tasks: List[Dict[str, Any]],
    workers: int,
    label: str,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not tasks:
        return results

    print(f"\n=== {label}: {len(tasks)} parallel games ===")
    with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
        futures = {pool.submit(run_match, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                record = {
                    "bot_key": task["bot_key"],
                    "bot_folder": task["bot_folder"],
                    "opponent": f"ai.{task['enemy_race']}.{task['difficulty']}",
                    "enemy_race": task["enemy_race"],
                    "difficulty": task["difficulty"],
                    "map": task["map_name"],
                    "port": task["port"],
                    "repeat_index": task.get("repeat_index", 1),
                    "status": "error",
                    "error": str(exc),
                    "victory": False,
                }
            results.append(record)
            status = record.get("result", record.get("error", "unknown"))
            victory = "WIN" if record.get("victory") else "LOSS" if record.get("status") == "ok" else "ERR"
            print(
                f"  [{victory}] {record.get('bot_key', '?')} vs {record.get('opponent', '?')} "
                f"run {record.get('repeat_index', '?')}: {status}"
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Terran BO ability sequences vs ingame AI.")
    parser.add_argument(
        "--output",
        default=os.path.join("bo_collection_runs", datetime.now().strftime("%Y-%m-%d_%H_%M_%S")),
        help="Root output directory.",
    )
    parser.add_argument("--map", default=None, help="Fixed map name (default: random from known melee maps).")
    parser.add_argument("--bots", nargs="*", default=None, help="Subset of bot keys or folder names.")
    parser.add_argument("--port-offset", type=int, default=BASE_PORT, help="Starting SC2 port base.")
    parser.add_argument("--workers", type=int, default=15, help="Parallel games per bot.")
    parser.add_argument(
        "--parallel-bots",
        action="store_true",
        help="Run all bot matchups in one shared pool (true cross-bot concurrency).",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Repeat each race/difficulty matchup N times.")
    parser.add_argument("--races", nargs="*", default=None, help="Subset of races (protoss,zerg,terran). Default: all three.")
    parser.add_argument(
        "--difficulties",
        nargs="*",
        default=None,
        help="AI difficulties (e.g. veryeasy easy medium mediumhard hard). Default: medium..veryhard.",
    )
    parser.add_argument(
        "--enemy-build",
        default="random",
        help="Ingame AI build style (random, macro, rush, timing, power, air). Default: random.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip matchups that already have a valid sequence JSON on disk.",
    )
    parser.add_argument(
        "--cleanup-stale",
        action="store_true",
        help="Delete orphan logs/replays for matchups that will be (re)run.",
    )
    args = parser.parse_args()

    output_root = os.path.abspath(args.output)
    os.makedirs(output_root, exist_ok=True)

    all_tasks, maps_used = build_tasks(
        output_root,
        args.bots,
        args.map,
        args.port_offset,
        difficulties=args.difficulties,
        enemy_build=args.enemy_build,
        repeats=args.repeats,
    )

    # Apply race filter
    if args.races:
        allowed_races = set(args.races)
        before = len(all_tasks)
        all_tasks = [t for t in all_tasks if t["enemy_race"] in allowed_races]
        print(f"Race filter {sorted(allowed_races)}: {before} -> {len(all_tasks)} tasks")

    bot_groups = group_tasks_by_bot(all_tasks)

    pending_tasks = filter_pending_tasks(all_tasks, args.skip_existing, args.cleanup_stale)
    skipped = len(all_tasks) - len(pending_tasks)

    print(f"Output root: {output_root}")
    print(f"Map: {maps_used[0]}")
    print(f"Bots: {len(bot_groups)}, games per bot: {len(bot_groups[0]) if bot_groups else 0}")
    print(f"Total games: {len(all_tasks)}")
    if args.skip_existing:
        print(f"Pending games: {len(pending_tasks)} (skipped {skipped} existing)")

    all_results: List[Dict[str, Any]] = []

    if args.parallel_bots:
        for task in all_tasks:
            bot_dir = os.path.join(output_root, task["bot_folder"])
            os.makedirs(bot_dir, exist_ok=True)
        batch_results = run_task_batch(
            pending_tasks,
            args.workers,
            f"All bots ({len(bot_groups)} strategies)",
        )
        for group in bot_groups:
            bot_folder = group[0]["bot_folder"]
            bot_dir = os.path.join(output_root, bot_folder)
            existing = _load_bot_results(bot_dir)
            bot_results = [r for r in batch_results if r.get("bot_folder") == bot_folder]
            merged = _merge_bot_results(existing, bot_results)
            save_bot_summary(bot_dir, merged)
            all_results.extend(merged)
    else:
        for group in bot_groups:
            bot_folder = group[0]["bot_folder"]
            bot_key = group[0]["bot_key"]
            bot_dir = os.path.join(output_root, bot_folder)
            os.makedirs(bot_dir, exist_ok=True)

            pending_group = filter_pending_tasks(group, args.skip_existing, args.cleanup_stale)
            existing = _load_bot_results(bot_dir)
            if pending_group:
                group_results = run_task_batch(
                    pending_group,
                    args.workers,
                    f"Bot {bot_key} ({bot_folder})",
                )
                merged = _merge_bot_results(existing, group_results)
            else:
                merged = existing
                print(f"\n=== Bot {bot_key} ({bot_folder}): all {len(group)} games already complete, skipped ===")
            all_results.extend(merged)
            save_bot_summary(bot_dir, merged)

    summary_path = save_summary(output_root, all_results)
    wins = sum(1 for r in all_results if r.get("victory"))
    print(f"\nDone. {wins}/{len(all_results)} victories. Summary: {summary_path}")


if __name__ == "__main__":
    main()
