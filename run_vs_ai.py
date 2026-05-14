"""启动与内置 AI 的自定义对战（可单独运行，也可由批处理脚本调用）。"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional, Sequence

# 确保能正确导入 python-sc2 和 sharpy 相关模块
sys.path.insert(1, "python-sc2")

from bot_loader import GameStarter, BotDefinitions
from version import update_version_txt

OUTPUT_BASE_DIR = "./game_records"


def _safe_match_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value))


def build_match_id(
    *,
    timestamp: str,
    my_bot_name: str,
    enemy_race: str,
    enemy_difficulty: str,
    enemy_build: str,
    map_name: str,
    bot_race: str,
    top_model: str,
    mid_model: str,
    down_model: str,
    run_index: Optional[int],
) -> str:
    parts: Sequence[str] = (
        timestamp,
        my_bot_name,
        bot_race,
        "vs",
        enemy_race,
        enemy_difficulty,
        enemy_build,
        map_name,
        _safe_match_part(top_model or "no_top"),
        _safe_match_part(mid_model or "no_mid"),
        _safe_match_part(down_model or "no_down"),
    )
    mid = "_".join(_safe_match_part(p) for p in parts)
    if run_index is not None:
        mid = f"{mid}_run{run_index}"
    return mid


def play_vs_ai(
    *,
    my_bot_name: str = "universal_llm",
    map_name: str = "KairosJunctionLE",
    real_time: bool = False,
    enemy_race: str = "terran",
    enemy_difficulty: str = "hard",
    enemy_build: str = "macro",
    bot_instruct: str = "打一波 以 大和为主的攻击",
    bot_race: str = "terran",
    top_model: str = "DeepSeek-V4-pro-reasoning",
    mid_model: str = "DeepSeek-V4-pro-reasoning",
    down_model: str = "DeepSeek-V4-flash",
    batch_name: Optional[str] = None,
    run_index: Optional[int] = None,
    output_base_dir: str = OUTPUT_BASE_DIR,
    skip_version_update: bool = False,
) -> None:
    """组装参数并启动一局游戏。"""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root_dir)

    if not skip_version_update:
        update_version_txt()

    p2_string = f"ai.{enemy_race}.{enemy_difficulty}.{enemy_build}"
    p1_string = f"{my_bot_name}.{bot_race}" if my_bot_name == "universal_llm" else my_bot_name

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    match_id = build_match_id(
        timestamp=timestamp,
        my_bot_name=my_bot_name,
        enemy_race=enemy_race,
        enemy_difficulty=enemy_difficulty,
        enemy_build=enemy_build,
        map_name=map_name,
        bot_race=bot_race,
        top_model=top_model,
        mid_model=mid_model,
        down_model=down_model,
        run_index=run_index,
    )

    base = os.path.abspath(output_base_dir)
    if batch_name:
        batch_slug = _safe_match_part(batch_name)
        record_dir = os.path.join(base, batch_slug, match_id)
    else:
        record_dir = os.path.join(base, match_id)
    os.makedirs(record_dir, exist_ok=True)

    args: List[str] = [
        "run_custom.py",
        "-m",
        map_name,
        "-p1",
        p1_string,
        "-p2",
        p2_string,
        "--record-dir",
        record_dir,
        "--match-id",
        match_id,
    ]
    if real_time:
        args.append("-rt")
    if bot_instruct:
        args.extend(["--instruct", bot_instruct])
    if top_model:
        args.extend(["--top-model", top_model])
    if mid_model:
        args.extend(["--mid-model", mid_model])
    if down_model:
        args.extend(["--down-model", down_model])
    sys.argv = args

    print("==================================================")
    print(" 正在启动 SC2 对战...")
    print(f" 你的 Bot : {my_bot_name} ({bot_race})")
    print(f" 对手 AI  : {enemy_race.upper()} | 难度: {enemy_difficulty} | 风格: {enemy_build}")
    print(f" 比赛地图 : {map_name}")
    print(f" 输出目录 : {record_dir}")
    if batch_name:
        print(f" 批次名称 : {batch_name}")
    if run_index is not None:
        print(f" 批次序号 : {run_index}")
    if bot_instruct:
        print(f" 战术指令 : {bot_instruct}")
    if top_model:
        print(f" Top Model: {top_model}")
    if mid_model:
        print(f" Mid Model: {mid_model}")
    if down_model:
        print(f" Down Model: {down_model}")
    print("==================================================")

    ladder_bots_path = os.path.join(root_dir, "Bots")
    definitions: BotDefinitions = BotDefinitions(ladder_bots_path)

    starter = GameStarter(definitions)
    starter.play()


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="与内置 AI 对战；无参数时沿用默认配置。批量并发请用 run_vs_ai_batch.sh 或 run_vs_ai_batch_env.sh。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--my-bot-name", default="universal_llm", help="Bot 名称")
    p.add_argument("--map-name", default="KairosJunctionLE", help="地图名")
    p.add_argument("--real-time", action="store_true", help="实时模式")
    p.add_argument("--enemy-race", default="terran", help="对手种族")
    p.add_argument("--enemy-difficulty", default="hard", help="对手难度")
    p.add_argument("--enemy-build", default="macro", help="对手 AI 风格")
    p.add_argument("--bot-instruct", default="打一波 以 大和为主的攻击", help="战术指令")
    p.add_argument("--bot-race", default="terran", help="我方种族")
    p.add_argument("--top-model", default="DeepSeek-V4-pro-reasoning", help="Top Agent 模型 key")
    p.add_argument("--mid-model", default="DeepSeek-V4-pro-reasoning", help="Mid Agent 模型 key")
    p.add_argument("--down-model", default="DeepSeek-V4-flash", help="Down Agent 模型 key")
    p.add_argument(
        "--batch-name",
        default="",
        help="非空时日志写入 game_records/<batch-name>/...，便于批量区分",
    )
    p.add_argument(
        "--run-index",
        type=int,
        default=None,
        help="批量运行时序号，写入 match_id 避免同秒冲突",
    )
    p.add_argument(
        "--output-base-dir",
        default=OUTPUT_BASE_DIR,
        help="记录根目录（默认 game_records）",
    )
    p.add_argument(
        "--skip-version-update",
        action="store_true",
        help="批跑时由外层已更新 version 时可跳过",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ns = _parse_args(argv)
    play_vs_ai(
        my_bot_name=ns.my_bot_name,
        map_name=ns.map_name,
        real_time=ns.real_time,
        enemy_race=ns.enemy_race,
        enemy_difficulty=ns.enemy_difficulty,
        enemy_build=ns.enemy_build,
        bot_instruct=ns.bot_instruct,
        bot_race=ns.bot_race,
        top_model=ns.top_model,
        mid_model=ns.mid_model,
        down_model=ns.down_model,
        batch_name=ns.batch_name or None,
        run_index=ns.run_index,
        output_base_dir=ns.output_base_dir,
        skip_version_update=ns.skip_version_update,
    )


if __name__ == "__main__":
    main()
