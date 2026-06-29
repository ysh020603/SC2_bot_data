#!/usr/bin/env bash
# 4 个 Terran 策略 × 3 张地图 × 3 种族 × 5 难度，对手 Macro AI。
# 采集规则与 bo_collection_runs/2026-06-24_terran_6bots_3maps_macro 一致。
# 用法: bash tools/run_terran_4bots_3maps_macro_collect.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="${CONDA_ENV:-sharpy-sc2}"
SESSION="${TMUX_SESSION:-sc2_terran_4bots_macro_20260627}"
RUN_ID="${RUN_ID:-2026-06-27_terran_4bots_3maps_macro}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${ROOT}/bo_collection_runs/${RUN_ID}}"
WORKERS="${WORKERS:-20}"
PORT_OFFSET="${PORT_OFFSET:-25000}"

BOTS=(marine_rush rusty banshees raven_liberator_tank)
MAPS=(KairosJunctionLE AutomatonLE AbyssalReefLE)
RACES=(zerg protoss terran)
DIFFS=(veryeasy easy medium mediumhard hard)

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
echo \"Races: ${RACES[*]}\" | tee -a \"${MASTER_LOG}\" && \
echo \"Difficulties: ${DIFFS[*]}\" | tee -a \"${MASTER_LOG}\" && \
echo \"Enemy build: macro\" | tee -a \"${MASTER_LOG}\" && \
echo \"Workers: ${WORKERS}\" | tee -a \"${MASTER_LOG}\" && \
for MAP in ${MAPS[*]}; do \
  OUT=\"${OUTPUT_ROOT}/\${MAP}\"; \
  echo \"========== Starting map: \${MAP} -> \${OUT} ==========\" | tee -a \"${MASTER_LOG}\"; \
  python -m sft_pipeline.collect.run_collect \
    --output \"\${OUT}\" \
    --map \"\${MAP}\" \
    --bots ${BOTS[*]} \
    --races zerg protoss terran \
    --difficulties veryeasy easy medium mediumhard hard \
    --enemy-build macro \
    --workers ${WORKERS} \
    --port-offset ${PORT_OFFSET} \
    2>&1 | tee -a \"${MASTER_LOG}\" \"\${OUT}_run.log\"; \
  echo \"========== Finished map: \${MAP} ==========\" | tee -a \"${MASTER_LOG}\"; \
done && \
echo \"ALL MAPS DONE at \$(date -Iseconds)\" | tee -a \"${MASTER_LOG}\""

tmux new-session -d -s "$SESSION" -c "$ROOT" bash -lc "$RUN_CMD"

echo "已在 tmux 后台启动轨迹采集"
echo "  会话名: tmux attach -t $SESSION"
echo "  输出目录: $OUTPUT_ROOT"
echo "  主日志: $MASTER_LOG"
echo "  每图 60 局 (4 bots × 3 races × 5 diffs)，共 180 局"
