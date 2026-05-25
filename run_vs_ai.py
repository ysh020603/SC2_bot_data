"""启动与内置 AI 的自定义对战（可单独运行，也可由批处理脚本调用）。

**首选改法**：编辑本文件 **「运行配置」** 区的 ``DEFAULT_*`` 常量后执行
``python run_vs_ai.py``；无需记一长串 CLI 参数。

固定策略（绕过 t=0 Top Agent 的 LLM 选策略）
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
取消固定策略请传 ``--force-strategy none``。

批量脚本可通过环境变量 ``FORCE_STRATEGY`` 传入（见 ``run_vs_ai_batch.sh``）。

各层是否启用 SKILL（两段式 Skill 路由 / 消融实验）
--------------------------------------------------
优先级：``--disable-all-skills`` > ``--enable-skill-layers``。

``--disable-all-skills``
    完全关闭 Skill：Top/Mid 均不做 Phase 1 筛选，Phase 2 不注入 Skill（基线）。

``--enable-skill-layers {all,top_only,mid_only,none}``  （默认 ``all``）
    * ``all``      — Top(t=0) 与 Mid 均启用 Skill 路由
    * ``top_only`` — 仅 Top 层启用
    * ``mid_only`` — 仅 Mid 层启用
    * ``none``     — 两层均不启用 Skill 路由

``--disable-specific-skills-layers {all,top,mid,none}``  （默认 ``none``）
    在已启用 Skill 的层上，强制只用 ``generic`` 目录下的通用 Skill，不用策略专属 Skill：
    * ``top`` / ``mid`` — 仅禁用对应层的 Specific Skill
    * ``all``           — 两层均只用 Generic
    * ``none``          — 不限制（Specific + Generic 均可）

CLI 示例（仅 Top 启用 Skill，且 Top 只用 Generic；同时锁定 marine_rush）::

    python run_vs_ai.py \\
        --enable-skill-layers top_only \\
        --disable-specific-skills-layers top \\
        --force-strategy marine_rush

批量脚本对应环境变量：``DISABLE_ALL_SKILLS``、``ENABLE_SKILL_LAYERS``、
``DISABLE_SPECIFIC_SKILLS_LAYERS``、``FORCE_STRATEGY``（见 ``start_experiments.sh``）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional, Sequence

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
DEFAULT_BOT_INSTRUCT = "打一波 以 大和为主的攻击"  # 自然语言战术指令（传给 Top/Mid Agent）
DEFAULT_MAP_NAME = "KairosJunctionLE"
DEFAULT_REAL_TIME = False  # True：实时模式，便于人类观战

# --- 对战：内置 AI 对手 ---
DEFAULT_ENEMY_RACE = "terran"
DEFAULT_ENEMY_DIFFICULTY = "hard"  # 如 easy / medium / hard / veryhard
DEFAULT_ENEMY_BUILD = "macro"  # 内置 AI 风格，如 macro / rush 等

# --- LLM 模型（model_key，见项目模型配置）---
DEFAULT_TOP_MODEL = "Kimi-k2.5_base"
DEFAULT_MID_MODEL = "Kimi-k2.5_base"
DEFAULT_DOWN_MODEL = "Kimi-k2.5_base"

# --- 固定 t=0 策略（绕过 Top Agent 开局选策略的 LLM）---
# 填 SKILL/<种族>/ 下的文件夹名，如 safe_tvt_raven → SKILL/terran/safe_tvt_raven/
# 须与 DEFAULT_BOT_RACE 一致。空字符串 "" 表示不强制；CLI 可用 --force-strategy none 取消。
DEFAULT_FORCE_STRATEGY = "battle_cruisers"

# --- 其它 ---
DEFAULT_SKIP_VERSION_UPDATE = False  # True：跳过 version.txt 更新（批量并发时防 IO 锁）


def _resolve_force_strategy(explicit: Optional[str]) -> Optional[str]:
    """解析 force_strategy。

    * ``explicit is None`` — 未在 CLI/调用方指定，使用 ``DEFAULT_FORCE_STRATEGY``
    * ``''`` / ``'none'`` — 显式取消强制
    * 其它非空字符串 — 策略文件夹名
    """
    if explicit is None:
        explicit = DEFAULT_FORCE_STRATEGY
    s = str(explicit or "").strip()
    if not s or s.lower() == "none":
        return None
    return s


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
    my_bot_name: str = DEFAULT_MY_BOT_NAME,
    map_name: str = DEFAULT_MAP_NAME,
    real_time: bool = DEFAULT_REAL_TIME,
    enemy_race: str = DEFAULT_ENEMY_RACE,
    enemy_difficulty: str = DEFAULT_ENEMY_DIFFICULTY,
    enemy_build: str = DEFAULT_ENEMY_BUILD,
    bot_instruct: str = DEFAULT_BOT_INSTRUCT,
    bot_race: str = DEFAULT_BOT_RACE,
    top_model: str = DEFAULT_TOP_MODEL,
    mid_model: str = DEFAULT_MID_MODEL,
    down_model: str = DEFAULT_DOWN_MODEL,
    batch_name: Optional[str] = None,
    run_index: Optional[int] = None,
    output_base_dir: str = OUTPUT_BASE_DIR,
    skip_version_update: bool = DEFAULT_SKIP_VERSION_UPDATE,
    force_strategy: Optional[str] = None,
) -> None:
    force_strategy = _resolve_force_strategy(force_strategy)

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
    if force_strategy:
        args.extend(["--force-strategy", force_strategy])

    sys.argv = args

    print("==================================================")
    print(" 正在启动 SC2 Agent 对战...")
    print(f" ▷ 我方阵营 : {my_bot_name} ({bot_race})")
    print(f" ▷ 对手 AI  : {enemy_race.upper()} | 难度: {enemy_difficulty} | 风格: {enemy_build}")
    print(f" ▷ 比赛地图 : {map_name}")
    print(f" ▷ 战术指令 : {bot_instruct}")
    print(f" ▷ 大模型簇 : Top=[{top_model}], Mid=[{mid_model}], Down=[{down_model}]")
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
    p.add_argument("--bot-instruct", default=DEFAULT_BOT_INSTRUCT, help="战术指令")
    p.add_argument("--bot-race", default=DEFAULT_BOT_RACE, help="我方种族")
    p.add_argument("--top-model", default=DEFAULT_TOP_MODEL, help="Top Agent")
    p.add_argument("--mid-model", default=DEFAULT_MID_MODEL, help="Mid Agent")
    p.add_argument("--down-model", default=DEFAULT_DOWN_MODEL, help="Down Agent")
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
            f"强制锁定 t=0 策略（SKILL/<race>/<name> 文件夹名）；"
            f"未指定时默认 {DEFAULT_FORCE_STRATEGY!r}（见 DEFAULT_FORCE_STRATEGY）；"
            f"传 none 取消强制。"
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
        bot_instruct=ns.bot_instruct,
        bot_race=ns.bot_race,
        top_model=ns.top_model,
        mid_model=ns.mid_model,
        down_model=ns.down_model,
        batch_name=ns.batch_name or None,
        run_index=ns.run_index,
        output_base_dir=ns.output_base_dir,
        skip_version_update=ns.skip_version_update,
        force_strategy=ns.force_strategy,
    )

if __name__ == "__main__":
    main()