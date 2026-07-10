#!/usr/bin/env bash
# OLD + NEW 各 6 个 Terran 策略 × 3 张地图 × 3 种族 × 5 难度，对手 Macro AI。
# 采集规则与 bo_collection_runs/2026-07-05_terran_old7bots_3maps_macro 一致。
# 用法: bash tools/run_terran_old_new_6bots_3maps_macro_collect.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="${CONDA_ENV:-sharpy-sc2}"
SESSION="${TMUX_SESSION:-sc2_terran_old_new_6bots_macro_20260706}"
DATE_TAG="${DATE_TAG:-2026-07-06}"
OUTPUT_ROOT_OLD="${OUTPUT_ROOT_OLD:-${ROOT}/bo_collection_runs/${DATE_TAG}_terran_old6bots_3maps_macro}"
OUTPUT_ROOT_NEW="${OUTPUT_ROOT_NEW:-${ROOT}/bo_collection_runs/${DATE_TAG}_terran_new6bots_3maps_macro}"
WORKERS="${WORKERS:-50}"
PORT_OFFSET="${PORT_OFFSET:-25000}"

MAPS=(KairosJunctionLE AutomatonLE AbyssalReefLE)

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

mkdir -p "$OUTPUT_ROOT_OLD" "$OUTPUT_ROOT_NEW"

RUN_CMD="source \"\$HOME/miniconda3/etc/profile.d/conda.sh\" && \
conda activate ${CONDA_ENV} && \
export SC2PATH=/data2/SC2/StarCraftII/ && \
export PYTHONPATH=${ROOT}/python-sc2:\$PYTHONPATH && \
export PYTHONUTF8=1 && \
export PYTHONIOENCODING=utf-8 && \
cd \"${ROOT}\" && \
for BATCH in OLD NEW; do \
  if [ \"\$BATCH\" = OLD ]; then \
    OUTPUT_ROOT=\"${OUTPUT_ROOT_OLD}\"; \
    BOTS=\"banshees battle_cruisers bio cyclones marine_rush two_base_tanks\"; \
  else \
    OUTPUT_ROOT=\"${OUTPUT_ROOT_NEW}\"; \
    BOTS=\"raven_screams yamato_rust_fleet rusty_bio_mines blueflame_locks stim_rush_relay two_base_matrix_tanks\"; \
  fi; \
  MASTER_LOG=\"\${OUTPUT_ROOT}/master_run.log\"; \
  echo \"========== \${BATCH} batch: \${OUTPUT_ROOT} ==========\" | tee \"\${MASTER_LOG}\"; \
  echo \"Run ID: \$(basename \"\${OUTPUT_ROOT}\")\" | tee -a \"\${MASTER_LOG}\"; \
  echo \"Bots: \${BOTS}\" | tee -a \"\${MASTER_LOG}\"; \
  echo \"Maps: ${MAPS[*]}\" | tee -a \"\${MASTER_LOG}\"; \
  echo \"Races: zerg protoss terran\" | tee -a \"\${MASTER_LOG}\"; \
  echo \"Difficulties: veryeasy easy medium mediumhard hard\" | tee -a \"\${MASTER_LOG}\"; \
  echo \"Enemy build: macro\" | tee -a \"\${MASTER_LOG}\"; \
  echo \"Workers: ${WORKERS}\" | tee -a \"\${MASTER_LOG}\"; \
  for MAP in ${MAPS[*]}; do \
    OUT=\"\${OUTPUT_ROOT}/\${MAP}\"; \
    echo \"========== Starting map: \${MAP} -> \${OUT} ==========\" | tee -a \"\${MASTER_LOG}\"; \
    python -m sft_pipeline.collect.run_collect \
      --output \"\${OUT}\" \
      --map \"\${MAP}\" \
      --bots \${BOTS} \
      --races zerg protoss terran \
      --difficulties veryeasy easy medium mediumhard hard \
      --enemy-build macro \
      --skip-existing \
      --cleanup-stale \
      --workers ${WORKERS} \
      --port-offset ${PORT_OFFSET} \
      2>&1 | tee -a \"\${MASTER_LOG}\" \"\${OUT}_run.log\"; \
    echo \"========== Finished map: \${MAP} ==========\" | tee -a \"\${MASTER_LOG}\"; \
  done; \
  echo \"ALL MAPS DONE (\${BATCH}) at \$(date -Iseconds)\" | tee -a \"\${MASTER_LOG}\"; \
done && \
echo \"ALL BATCHES DONE at \$(date -Iseconds)\""

tmux new-session -d -s "$SESSION" -c "$ROOT" bash -lc "$RUN_CMD"

echo "已在 tmux 后台启动 OLD + NEW 轨迹采集"
echo "  会话名: tmux attach -t $SESSION"
echo "  OLD 输出: $OUTPUT_ROOT_OLD"
echo "  NEW 输出: $OUTPUT_ROOT_NEW"
echo "  每批 270 局 (6 bots × 3 races × 5 diffs × 3 maps)，共 540 局"
