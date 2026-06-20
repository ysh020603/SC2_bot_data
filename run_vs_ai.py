"""启动与内置 AI 的自定义对战（可单独运行，也可由批处理脚本调用）。

**首选改法**：编辑本文件 **「运行配置」** 区的 ``DEFAULT_*`` 常量后执行
``python run_vs_ai.py``；无需记一长串 CLI 参数。

固定策略
------------------------------------------------
使用 ``--force-strategy <文件夹名>``，或调用 ``play_vs_ai(force_strategy="...")``。
策略名对应 ``SKILL/<种族>/<文件夹名>/``（如人族 ``marine_rush`` → ``SKILL/terran/marine_rush/``）。
须保证 ``--bot-race`` 与策略目录种族一致，且 ``--my-bot-name`` 为 ``universal_llm``（默认）。

CLI 示例::

    python run_vs_ai.py --bot-race terran --force-strategy marine_rush

代码示例::

    play_vs_ai(bot_race="terran", force_strategy="marine_rush")

所有默认值集中在文件内 **「运行配置」** 常量区（``DEFAULT_*``），
直接 ``python run_vs_ai.py`` 即生效；CLI 显式传参会覆盖对应项。
当前版本必须指定固定策略或 BO list；``--force-strategy none`` 单独使用会报错。

批量脚本可通过环境变量 ``FORCE_STRATEGY`` 传入（见 ``run_vs_ai_batch.sh``）。

模型参数
------------------------------------------------
当前主线只保留五阶段增量流水线中的 LLM 调用点：
``--naming-model``、``--ordering-model``、``--executor-model``。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

sys.path.insert(1, "python-sc2")

from bot_loader import GameStarter, BotDefinitions
from version import update_version_txt

# =============================================================================
# 运行配置 — 改这里即可；``python run_vs_ai.py`` 与 ``play_vs_ai()`` 均以此为准
# CLI 显式传参会覆盖对应项。布尔项支持 ``--flag`` / ``--no-flag`` 覆盖文件默认。
# =============================================================================

OUTPUT_BASE_DIR = "./game_records"

# --- 对战：我方 ---
DEFAULT_MY_BOT_NAME = "universal_llm"  # universal_llm 时自动拼接种族：universal_llm.terran
DEFAULT_BOT_RACE = "terran"  # protoss | terran | zerg
DEFAULT_MAP_NAME = "KairosJunctionLE"
DEFAULT_REAL_TIME = False  # True：实时模式，便于人类观战

# --- 对战：内置 AI 对手 ---
DEFAULT_ENEMY_RACE = "terran"
DEFAULT_ENEMY_DIFFICULTY = "medium"  # 如 easy / medium / hard / veryhard
DEFAULT_ENEMY_BUILD = "random"  # 内置 AI 风格；random 对应 RandomBuild

# --- 五阶段增量驱动流水线各 LLM 调用点（model_key，见 API_config/config.json）---
DEFAULT_NAMING_MODEL = "DeepSeek-V4-flash"
DEFAULT_ORDERING_MODEL = "DeepSeek-V4-flash"
DEFAULT_EXECUTOR_MODEL = "DeepSeek-V4-flash"

# --- 固定策略 ---
# 填 SKILL/<种族>/ 下的文件夹名，如 safe_tvt_raven → SKILL/terran/safe_tvt_raven/
# 须与 DEFAULT_BOT_RACE 一致。运行时必须指定一个策略文件夹名 *或* 一个 BO list。
DEFAULT_FORCE_STRATEGY = "marine_rush"

# --- BO list 直接执行模式 ---
# 填 BO_list/<种族>/ 下的文件夹名（如 marine_rush → BO_list/terran/marine_rush/）。
# 非空时启用 BO 直接执行模式，跳过 Naming/Ordering 流水线（Executor LLM 仅 train 生效），
# 与 DEFAULT_FORCE_STRATEGY 互斥。``None``/`""` 表示不启用。
DEFAULT_BO_LIST: Optional[str] = None

# --- 其它 ---
DEFAULT_SKIP_VERSION_UPDATE = False  # True：跳过 version.txt 更新（批量并发时防 IO 锁）


def _resolve_strategy_modes(
    force_strategy: Optional[str],
    bo_list: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """解析 force_strategy / bo_list 两种互斥模式。

    规则：
    * 任一为 ``None`` 表示"调用方未显式指定"；按 ``DEFAULT_*`` 兜底。
    * 任一为 ``""`` / ``"none"`` 表示"显式关闭该模式"。
    * 解析后两者必须恰好一个非空。
    """
    fs_explicit = force_strategy is not None
    bo_explicit = bo_list is not None

    fs = (force_strategy if fs_explicit else DEFAULT_FORCE_STRATEGY) or ""
    bo = (bo_list if bo_explicit else DEFAULT_BO_LIST) or ""
    fs = fs.strip()
    bo = bo.strip()
    if fs.lower() == "none":
        fs = ""
    if bo.lower() == "none":
        bo = ""

    # 当用户在 CLI 显式给了 --bo-list 而未给 --force-strategy 时，
    # 自动屏蔽 DEFAULT_FORCE_STRATEGY，避免两个模式同时生效。
    if bo_explicit and not fs_explicit and bo:
        fs = ""
    if fs_explicit and not bo_explicit and fs:
        bo = ""

    if fs and bo:
        raise ValueError(
            "force_strategy and bo_list are mutually exclusive; "
            "specify exactly one."
        )
    if not fs and not bo:
        raise ValueError(
            "Either force_strategy or bo_list is required; "
            "pass a folder name under SKILL/<race>/ or BO_list/<race>/."
        )
    return (fs or None), (bo or None)


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
    naming_model: str,
    ordering_model: str,
    executor_model: str,
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
        _safe_match_part(naming_model or "no_naming"),
        _safe_match_part(ordering_model or "no_ordering"),
        _safe_match_part(executor_model or "no_executor"),
    )
    match_str = "_".join(_safe_match_part(p) for p in parts)
    if run_index is not None:
        match_str = f"{match_str}_run{run_index}"
    return match_str

def play_vs_ai(
    *,
    my_bot_name: str = DEFAULT_MY_BOT_NAME,
    map_name: str = DEFAULT_MAP_NAME,
    real_time: bool = DEFAULT_REAL_TIME,
    enemy_race: str = DEFAULT_ENEMY_RACE,
    enemy_difficulty: str = DEFAULT_ENEMY_DIFFICULTY,
    enemy_build: str = DEFAULT_ENEMY_BUILD,
    bot_race: str = DEFAULT_BOT_RACE,
    naming_model: str = DEFAULT_NAMING_MODEL,
    ordering_model: str = DEFAULT_ORDERING_MODEL,
    executor_model: str = DEFAULT_EXECUTOR_MODEL,
    batch_name: Optional[str] = None,
    run_index: Optional[int] = None,
    output_base_dir: str = OUTPUT_BASE_DIR,
    skip_version_update: bool = DEFAULT_SKIP_VERSION_UPDATE,
    force_strategy: Optional[str] = None,
    bo_list: Optional[str] = None,
) -> None:
    force_strategy, bo_list = _resolve_strategy_modes(force_strategy, bo_list)

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
        naming_model=naming_model,
        ordering_model=ordering_model,
        executor_model=executor_model,
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
    if naming_model:
        args.extend(["--naming-model", naming_model])
    if ordering_model:
        args.extend(["--ordering-model", ordering_model])
    if executor_model:
        args.extend(["--executor-model", executor_model])
    if force_strategy:
        args.extend(["--force-strategy", force_strategy])
    if bo_list:
        args.extend(["--bo-list", bo_list])

    sys.argv = args

    print("==================================================")
    print(" 正在启动 SC2 Agent 对战...")
    print(f" ▷ 我方阵营 : {my_bot_name} ({bot_race})")
    print(f" ▷ 对手 AI  : {enemy_race.upper()} | 难度: {enemy_difficulty} | 风格: {enemy_build}")
    print(f" ▷ 比赛地图 : {map_name}")
    print(
        f" ▷ 流水线簇 : Naming=[{naming_model}], "
        f"Ordering=[{ordering_model}], Executor=[{executor_model}]"
    )
    if bo_list:
        print(f" ▷ 运行模式 : BO list 直接执行 ({bo_list})")
    else:
        print(f" ▷ 强制策略 : {force_strategy or 'None'}")
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
    p.add_argument("--my-bot-name", default=DEFAULT_MY_BOT_NAME, help="Bot 名称")
    p.add_argument("--map-name", default=DEFAULT_MAP_NAME, help="地图名")
    p.add_argument(
        "--real-time",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_REAL_TIME,
        help="实时模式(人类观测)",
    )
    p.add_argument("--enemy-race", default=DEFAULT_ENEMY_RACE, help="对手种族")
    p.add_argument("--enemy-difficulty", default=DEFAULT_ENEMY_DIFFICULTY, help="对手难度")
    p.add_argument("--enemy-build", default=DEFAULT_ENEMY_BUILD, help="对手 AI 风格")
    p.add_argument("--bot-race", default=DEFAULT_BOT_RACE, help="我方种族")
    p.add_argument("--naming-model", default=DEFAULT_NAMING_MODEL, help="Naming Agent (stage 2)")
    p.add_argument("--ordering-model", default=DEFAULT_ORDERING_MODEL, help="Ordering Agent (stage 4)")
    p.add_argument("--executor-model", default=DEFAULT_EXECUTOR_MODEL, help="Executor Agent (train)")
    p.add_argument("--batch-name", default="", help="记录写入 game_records/<batch-name>/ 归档")
    p.add_argument("--run-index", type=int, default=None, help="批处理序号以防并发冲突")
    p.add_argument("--output-base-dir", default=OUTPUT_BASE_DIR, help="记录根目录")
    p.add_argument(
        "--skip-version-update",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_SKIP_VERSION_UPDATE,
        help="跳过版本更新防止 IO 锁",
    )
    p.add_argument(
        "--force-strategy",
        default=None,
        metavar="NAME",
        help=(
            "Strategy folder name under SKILL/<race>/; "
            f"default is {DEFAULT_FORCE_STRATEGY!r}. "
            "Mutually exclusive with --bo-list."
        ),
    )
    p.add_argument(
        "--bo-list",
        default=None,
        metavar="NAME",
        help=(
            "BO list folder name under BO_list/<race>/. When set, the bot "
            "skips Naming/Ordering LLM stages and feeds BO.json directly "
            "into the execution scheduler. Mutually exclusive with --force-strategy."
        ),
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
        bot_race=ns.bot_race,
        naming_model=ns.naming_model,
        ordering_model=ns.ordering_model,
        executor_model=ns.executor_model,
        batch_name=ns.batch_name or None,
        run_index=ns.run_index,
        output_base_dir=ns.output_base_dir,
        skip_version_update=ns.skip_version_update,
        force_strategy=ns.force_strategy,
        bo_list=ns.bo_list,
    )

if __name__ == "__main__":
    main()
