#!/usr/bin/env bash
# 10 个 Terran 策略 × 3 张地图 × 3 种族 × 5 难度，仅采集轨迹（不做 step 标注）。
# 对手为内置 AI，RandomBuild 风格（collect_terran_bo 默认 ai.race.difficulty -> RandomBuild）。
# 用法: bash tools/run_terran_10bots_3maps_collect.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="${CONDA_ENV:-sharpy-sc2}"
SESSION="${TMUX_SESSION:-sc2_terran_bo_collect_20260622}"
RUN_ID="${RUN_ID:-2026-06-22_terran_10bots_3maps}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${ROOT}/bo_collection_runs/${RUN_ID}}"
WORKERS="${WORKERS:-20}"
PORT_OFFSET="${PORT_OFFSET:-25000}"

BOTS=(
  bio safe_tvt_raven three_rax_stim two_base_tanks tank_thor_mech
  battle_cruisers marine_rush rusty banshees raven_liberator_tank
)
MAPS=(KairosJunctionLE AutomatonLE AbyssalReefLE)
RACES=(zerg protoss terran)
DIFFS=(medium mediumhard hard harder veryhard)

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux 会话已存在: $SESSION"
  echo "  附加: tmux attach -t $SESSION"
  exit 1
fi

if pgrep -f "tools/collect_terran_bo.py" >/dev/null; then
  echo "已有 collect_terran_bo.py 在运行，请先确认后再启动。"
  pgrep -af "tools/collect_terran_bo.py" || true
  exit 1
fi

mkdir -p "$OUTPUT_ROOT"
MASTER_LOG="${OUTPUT_ROOT}/master_run.log"

RUN_CMD="source \"\$HOME/miniconda3/etc/profile.d/conda.sh\" && \
conda activate ${CONDA_ENV} && \
export SC2PATH=/data2/SC2/StarCraftII/ && \
export PYTHONPATH=${ROOT}/python-sc2:\$PYTHONPATH && \
export PYTHONUTF8=1 && \
export PYTHONIOENCODING=utf-8 && \
cd \"${ROOT}\" && \
echo \"Run ID: ${RUN_ID}\" | tee \"${MASTER_LOG}\" && \
echo \"Bots: ${BOTS[*]}\" | tee -a \"${MASTER_LOG}\" && \
echo \"Maps: ${MAPS[*]}\" | tee -a \"${MASTER_LOG}\" && \
echo \"Difficulties: ${DIFFS[*]}\" | tee -a \"${MASTER_LOG}\" && \
echo \"Workers: ${WORKERS}\" | tee -a \"${MASTER_LOG}\" && \
for MAP in ${MAPS[*]}; do \
  OUT=\"${OUTPUT_ROOT}/\${MAP}\"; \
  echo \"========== Starting map: \${MAP} -> \${OUT} ==========\" | tee -a \"${MASTER_LOG}\"; \
  python -m sft_pipeline.collect.run_collect \
    --output \"\${OUT}\" \
    --map \"\${MAP}\" \
    --bots ${BOTS[*]} \
    --races zerg protoss terran \
    --difficulties medium mediumhard hard harder veryhard \
    --workers ${WORKERS} \
    --port-offset ${PORT_OFFSET} \
    2>&1 | tee -a \"${MASTER_LOG}\" \"\${OUT}_run.log\"; \
  echo \"========== Finished map: \${MAP} ==========\" | tee -a \"${MASTER_LOG}\"; \
done && \
echo \"ALL MAPS DONE at \$(date -Iseconds)\" | tee -a \"${MASTER_LOG}\""

tmux new-session -d -s "$SESSION" -c "$ROOT" bash -lc "$RUN_CMD"

echo "已在 tmux 后台启动轨迹采集"
echo "  会话名(self): tmux attach -t $SESSION"
echo "  输出目录: $OUTPUT_ROOT"
echo "  主日志: $MASTER_LOG"
echo "  每图 150 局 (10 bots × 3 races × 5 diffs)，共 450 局"
