#!/usr/bin/env bash
# start_experiments.sh
# Configure a batch of SC2 Agent matches, then launch run_vs_ai_batch.sh.

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Python runtime
# ---------------------------------------------------------------------------
# Override this before running if needed, for example:
#   export PYTHON=/path/to/conda/env/bin/python
export PYTHON="${PYTHON:-python3}"

# ---------------------------------------------------------------------------
# 1. Match configuration
# ---------------------------------------------------------------------------
export MY_BOT_NAME="${MY_BOT_NAME:-universal_llm}"
export MAP_NAME="${MAP_NAME:-KairosJunctionLE}"
export REAL_TIME="${REAL_TIME:-0}"  # 1 = realtime, 0 = accelerated

export ENEMY_RACE="${ENEMY_RACE:-zerg}"
export ENEMY_DIFFICULTY="${ENEMY_DIFFICULTY:-harder}"
export ENEMY_BUILD="${ENEMY_BUILD:-air}"

export BOT_RACE="${BOT_RACE:-terran}"
export FORCE_STRATEGY="${FORCE_STRATEGY:-marine_rush}"

# ---------------------------------------------------------------------------
# 2. Five-stage pipeline model keys
# ---------------------------------------------------------------------------
export NAMING_MODEL="${NAMING_MODEL:-DeepSeek-V4-flash}"
export ORDERING_MODEL="${ORDERING_MODEL:-DeepSeek-V4-flash}"
export EXECUTOR_MODEL="${EXECUTOR_MODEL:-DeepSeek-V4-flash}"

# ---------------------------------------------------------------------------
# 3. Batch controls
# ---------------------------------------------------------------------------
TOTAL_MATCHES="${TOTAL_MATCHES:-10}"
CONCURRENCY="${CONCURRENCY:-10}"
RUN_MODE="${RUN_MODE:-tmux}"  # tmux or fg

export BATCH_NAME="${BATCH_NAME:-}"

echo "Starting SC2 Agent batch..."
bash ./run_vs_ai_batch.sh "$TOTAL_MATCHES" "$CONCURRENCY" "$RUN_MODE"
