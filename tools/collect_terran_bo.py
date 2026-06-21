#!/usr/bin/env python3
"""Batch-collect ability-sequence BO data for Terran dummy bots.

Runs each bot against all three races at medium/mediumhard/hard/harder/veryhard AI difficulty.
Within each bot, all matchups are launched in parallel; bots are processed sequentially.
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
]

RACES = ("protoss", "zerg", "terran")
DIFFICULTIES = ("medium", "mediumhard", "hard", "harder", "veryhard")
BASE_PORT = 25000
PORT_STRIDE = 8


def _make_config(sequence_dir: str, log_file: bool) -> ConfigParser:
    config = deepcopy(get_config())
    config["general"]["write_ability_sequence"] = "yes"
    config["general"]["ability_sequence_dir"] = sequence_dir
    config["general"]["log_file"] = "yes" if log_file else "no"
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

    bot_dir = os.path.join(output_root, bot_folder)
    seq_dir = os.path.join(bot_dir, "sequences")
    log_dir = os.path.join(bot_dir, "logs")
    replay_dir = os.path.join(bot_dir, "replays")
    for folder in (seq_dir, log_dir, replay_dir):
        os.makedirs(folder, exist_ok=True)

    player1_id = bot_key
    player2_id = f"ai.{enemy_race}.{difficulty}"
    stamp = datetime.now().strftime("%Y-%m-%d %H_%M_%S")
    tag = random.randint(0, 999999)
    base_name = f"{bot_key}-{player2_id}_{map_name}_{stamp}_{tag}"
    log_path = os.path.join(log_dir, f"{base_name}.log")
    replay_path = os.path.join(replay_dir, f"{base_name}.SC2Replay")

    result_record: Dict[str, Any] = {
        "bot_key": bot_key,
        "bot_folder": bot_folder,
        "opponent": player2_id,
        "enemy_race": enemy_race,
        "difficulty": difficulty,
        "map": map_name,
        "port": port,
        "log_path": log_path,
        "replay_path": replay_path,
        "sequence_dir": seq_dir,
        "status": "error",
        "result": None,
        "error": None,
        "sequence_file": None,
    }

    try:
        definitions = BotDefinitions()
        playable = definitions.playable

        config = _make_config(seq_dir, log_file=True)
        LoggingUtility.set_logger_file(log_level=config["general"]["log_level"], path=log_path)

        player1_bot = playable[bot_key]([])
        player2_bot = playable["ai"]([enemy_race, difficulty])

        GameStarter.setup_bot(player1_bot, player1_id, player2_id, argparse.Namespace(raw_selection=False, release=False))
        GameStarter.setup_bot(player2_bot, player2_id, player1_id, argparse.Namespace(raw_selection=False, release=False))

        if isinstance(player1_bot, Bot) and hasattr(player1_bot.ai, "config"):
            my_bot: KnowledgeBot = player1_bot.ai
            my_bot.config = config

        seq_before = set(os.listdir(seq_dir)) if os.path.isdir(seq_dir) else set()

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

        seq_after = set(os.listdir(seq_dir)) if os.path.isdir(seq_dir) else set()
        new_files = sorted(seq_after - seq_before)
        if new_files:
            result_record["sequence_file"] = os.path.join(seq_dir, new_files[-1])

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

    tasks: List[Dict[str, Any]] = []
    port_idx = 0
    for bot_key, bot_folder in bots:
        for enemy_race in RACES:
            for difficulty in DIFFICULTIES:
                tasks.append(
                    {
                        "bot_key": bot_key,
                        "bot_folder": bot_folder,
                        "enemy_race": enemy_race,
                        "difficulty": difficulty,
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
    parser.add_argument("--races", nargs="*", default=None, help="Subset of races (protoss,zerg,terran). Default: all three.")
    parser.add_argument("--difficulties", nargs="*", default=None, help="Subset of difficulties (medium,mediumhard,hard,harder,veryhard). Default: all five.")
    args = parser.parse_args()

    output_root = os.path.abspath(args.output)
    os.makedirs(output_root, exist_ok=True)

    all_tasks, maps_used = build_tasks(output_root, args.bots, args.map, args.port_offset)

    # Apply race/difficulty filters
    if args.races:
        allowed_races = set(args.races)
        before = len(all_tasks)
        all_tasks = [t for t in all_tasks if t["enemy_race"] in allowed_races]
        print(f"Race filter {sorted(allowed_races)}: {before} -> {len(all_tasks)} tasks")
    if args.difficulties:
        allowed_diffs = set(args.difficulties)
        before = len(all_tasks)
        all_tasks = [t for t in all_tasks if t["difficulty"] in allowed_diffs]
        print(f"Difficulty filter {sorted(allowed_diffs)}: {before} -> {len(all_tasks)} tasks")

    bot_groups = group_tasks_by_bot(all_tasks)

    print(f"Output root: {output_root}")
    print(f"Map: {maps_used[0]}")
    print(f"Bots: {len(bot_groups)}, games per bot: {len(bot_groups[0]) if bot_groups else 0}")
    print(f"Total games: {len(all_tasks)}")

    all_results: List[Dict[str, Any]] = []

    for group in bot_groups:
        bot_folder = group[0]["bot_folder"]
        bot_key = group[0]["bot_key"]
        print(f"\n=== Bot {bot_key} ({bot_folder}): {len(group)} parallel games ===")
        bot_dir = os.path.join(output_root, bot_folder)
        os.makedirs(bot_dir, exist_ok=True)

        with ProcessPoolExecutor(max_workers=min(args.workers, len(group))) as pool:
            futures = {pool.submit(run_match, task): task for task in group}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    record = {
                        "bot_key": task["bot_key"],
                        "opponent": f"ai.{task['enemy_race']}.{task['difficulty']}",
                        "status": "error",
                        "error": str(exc),
                        "victory": False,
                    }
                all_results.append(record)
                status = record.get("result", record.get("error", "unknown"))
                victory = "WIN" if record.get("victory") else "LOSS" if record.get("status") == "ok" else "ERR"
                print(f"  [{victory}] {record.get('opponent', '?')}: {status}")

        bot_results = [r for r in all_results if r.get("bot_folder") == bot_folder]
        save_bot_summary(bot_dir, bot_results)

    summary_path = save_summary(output_root, all_results)
    wins = sum(1 for r in all_results if r.get("victory"))
    print(f"\nDone. {wins}/{len(all_results)} victories. Summary: {summary_path}")


if __name__ == "__main__":
    main()
