"""启动与内置 AI 的自定义对战（可单独运行，也可由批处理脚本调用）。"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional, Sequence

sys.path.insert(1, "python-sc2")

from bot_loader import GameStarter, BotDefinitions
from version import update_version_txt

OUTPUT_BASE_DIR = "./game_records"

# 分层 LLM 模型默认值：改此处即可；CLI（python run_vs_ai.py）与 play_vs_ai() 均生效
DEFAULT_TOP_MODEL = "Kimi-k2.5_base"
DEFAULT_MID_MODEL = "Kimi-k2.5_base"
DEFAULT_DOWN_MODEL = "Kimi-k2.5_base"

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
    match_str = "_".join(_safe_match_part(p) for p in parts)
    if run_index is not None:
        match_str = f"{match_str}_run{run_index}"
    return match_str

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
    top_model: str = DEFAULT_TOP_MODEL,
    mid_model: str = DEFAULT_MID_MODEL,
    down_model: str = DEFAULT_DOWN_MODEL,
    batch_name: Optional[str] = None,
    run_index: Optional[int] = None,
    output_base_dir: str = OUTPUT_BASE_DIR,
    skip_version_update: bool = False,
) -> None:
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
    # 如果指定了 batch_name，则归档到单独的批次文件夹下面
    if batch_name:
        batch_slug = _safe_match_part(batch_name)
        record_dir = os.path.join(base, batch_slug, match_id)
    else:
        record_dir = os.path.join(base, match_id)
        
    os.makedirs(record_dir, exist_ok=True)

    args: List[str] = [
        "run_custom.py",
        "-m", map_name,
        "-p1", p1_string,
        "-p2", p2_string,
        "--record-dir", record_dir,
        "--match-id", match_id,
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
    print(" 正在启动 SC2 Agent 对战...")
    print(f" ▷ 我方阵营 : {my_bot_name} ({bot_race})")
    print(f" ▷ 对手 AI  : {enemy_race.upper()} | 难度: {enemy_difficulty} | 风格: {enemy_build}")
    print(f" ▷ 比赛地图 : {map_name}")
    print(f" ▷ 战术指令 : {bot_instruct}")
    print(f" ▷ 大模型簇 : Top=[{top_model}], Mid=[{mid_model}], Down=[{down_model}]")
    if batch_name:
        print(f" ▷ 批次名称 : {batch_name} (任务序号: {run_index})")
    print(f" ▷ 记录目录 : {record_dir}")
    print("==================================================")

    ladder_bots_path = os.path.join(root_dir, "Bots")
    definitions: BotDefinitions = BotDefinitions(ladder_bots_path)

    starter = GameStarter(definitions)
    starter.play()

def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="与 SC2 内置 AI 对战。支持单跑或被批处理脚本调用。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--my-bot-name", default="universal_llm", help="Bot 名称")
    p.add_argument("--map-name", default="KairosJunctionLE", help="地图名")
    p.add_argument("--real-time", action="store_true", help="实时模式(人类观测)")
    p.add_argument("--enemy-race", default="terran", help="对手种族")
    p.add_argument("--enemy-difficulty", default="hard", help="对手难度")
    p.add_argument("--enemy-build", default="macro", help="对手 AI 风格")
    p.add_argument("--bot-instruct", default="打一波 以 大和为主的攻击", help="战术指令")
    p.add_argument("--bot-race", default="terran", help="我方种族")
    p.add_argument("--top-model", default=DEFAULT_TOP_MODEL, help="Top Agent")
    p.add_argument("--mid-model", default=DEFAULT_MID_MODEL, help="Mid Agent")
    p.add_argument("--down-model", default=DEFAULT_DOWN_MODEL, help="Down Agent")
    p.add_argument("--batch-name", default="", help="记录写入 game_records/<batch-name>/ 归档")
    p.add_argument("--run-index", type=int, default=None, help="批处理序号以防并发冲突")
    p.add_argument("--output-base-dir", default=OUTPUT_BASE_DIR, help="记录根目录")
    p.add_argument("--skip-version-update", action="store_true", help="跳过版本更新防止 IO 锁")
    
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