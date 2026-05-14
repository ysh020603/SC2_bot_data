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

# 对手配置
export ENEMY_RACE="terran"
export ENEMY_DIFFICULTY="hard"
export ENEMY_BUILD="macro"

# 我方 Agent 配置
export BOT_RACE="terran"
export BOT_INSTRUCT="打一波，以大和战列巡洋舰为主的攻击"

# =============================================================================
# 2. 分层 LLM Agent 模型配置
# =============================================================================
export TOP_MODEL="qwen2.5_14B"
export MID_MODEL="qwen2.5_14B"
export DOWN_MODEL="DeepSeek-V4-flash"

# =============================================================================
# 3. 运行控制 (总局数 / 并发数 / 运行模式)
# =============================================================================
TOTAL_MATCHES=8     # 运行的总局数
CONCURRENCY=2        # 并发执行的数量
RUN_MODE="tmux"      # 选项: 'tmux' (推荐,每个窗口一个线程) 或 'fg' (当前终端后台运行)

# 批次名称(可选)，留空则会自动根据上方配置生成带时间戳和模型信息的文件夹名
export BATCH_NAME="" 

echo "正在应用配置并启动批处理任务..."
bash ./run_vs_ai_batch.sh "$TOTAL_MATCHES" "$CONCURRENCY" "$RUN_MODE"