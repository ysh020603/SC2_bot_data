#!/usr/bin/env bash
# start_experiments.sh
# 专门用于设置参数、大模型配置，并启动批量对战。

set -e

# =============================================================================
# 0. 核心配置：指定 Conda 环境的 Python 绝对路径 (解决 tmux 环境迷失问题)
# =============================================================================
# 请在你的终端激活 hw 环境后，输入 `which python` 获取该路径并替换下方变量：
export PYTHON="/home/wyq/miniconda3/envs/hw/bin/python" 

# =============================================================================
# 1. 游戏与战术配置
# =============================================================================
export MY_BOT_NAME="universal_llm"
export MAP_NAME="KairosJunctionLE"
export REAL_TIME="0" # 1为实时模式，0为加速模式


# "veryeasy",
# "easy",
# "medium",
# "mediumhard",
# "hard",
# "harder",
# "veryhard",
# "cheatvision",
# "cheatmoney",
# "cheatinsane"
# 对手配置
# "protoss"
# "zerg"
# "terran"

export ENEMY_RACE="terran"
export ENEMY_DIFFICULTY="hard"
export ENEMY_BUILD="macro"

# 我方 Agent 配置
export BOT_RACE="terran"
export BOT_INSTRUCT="打一波，以大和战列巡洋舰为主的攻击"

# =============================================================================
# 2. 分层 LLM Agent 模型配置
# =============================================================================
export TOP_MODEL="DeepSeek-V4-pro"
export MID_MODEL="DeepSeek-V4-pro"
export DOWN_MODEL="DeepSeek-V4-flash"

# =============================================================================
# 2.1 阶段性 Prompt 注入开关（1=开启，0=关闭）—— 仅在两段式 Skill 路由被关闭时生效。
#  - USE_TOP_60_PROMPT：t=60 阶段评估时是否拼接 Top_agent_60.md 作为 [Phase Guidance]。
#  - USE_MID_PROMPT   ：Mid Agent 规划时是否拼接 mid_agent.md  作为 [Execution Guidance]。
# =============================================================================
export USE_TOP_60_PROMPT="0"
export USE_MID_PROMPT="0"

# =============================================================================
# 2.2 两段式 Skill 路由 / 消融实验开关 (Module 3)
#  - DISABLE_ALL_SKILLS              ：1 = 跳过 Phase 1 筛选；Phase 2 不注入 Skill。
#                                      此时整体退化为原始基线（仅旧字段 USE_TOP_60_PROMPT
#                                      / USE_MID_PROMPT 仍可生效）。
#  - ENABLE_SKILL_LAYERS             ：哪一层启用两段式 Skill：
#                                      all / top_only / mid_only / none。
#  - DISABLE_SPECIFIC_SKILLS_LAYERS  ：哪一层禁用 Specific Skill（仅用 Generic）：
#                                      all / top / mid / none。
#  - FORCE_STRATEGY                  ：强制锁定的 t=0 策略名（如 marine_rush）。
#                                      留空表示走正常 T=0 LLM 选择/生成流程。
# =============================================================================
export DISABLE_ALL_SKILLS="1"
export ENABLE_SKILL_LAYERS="all"
export DISABLE_SPECIFIC_SKILLS_LAYERS="none"
export FORCE_STRATEGY="battle_cruisers"

# =============================================================================
# 3. 运行控制 (总局数 / 并发数 / 运行模式)
# =============================================================================
TOTAL_MATCHES=2     # 运行的总局数
CONCURRENCY=2        # 并发执行的数量
RUN_MODE="tmux"      # 选项: 'tmux' (推荐,每个窗口一个线程) 或 'fg' (当前终端后台运行)

# 批次名称(可选)，留空则会自动根据上方配置生成带时间戳和模型信息的文件夹名
export BATCH_NAME="" 

echo "正在应用配置并启动批处理任务..."
bash ./run_vs_ai_batch.sh "$TOTAL_MATCHES" "$CONCURRENCY" "$RUN_MODE"