#!/usr/bin/env bash
# v8 step 标注流水线：Obs QA → 按地图分批 Kimi 标注 → 失败重试 → v8 QA
#
# 排序规则：diverse-hard-first（策略轮询 + 高难度优先 + 种族均衡）
# 地图顺序：默认 KairosJunctionLE → AutomatonLE → AbyssalReefLE
#
# 用法:
#   DATA_DIR=bo_collection_runs/<run_id> \
#   OUTPUT=sft_pipeline_outputs/<run_id>/v8_steps \
#   bash tools/run_v8_label_pipeline.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="${CONDA_ENV:-sharpy-sc2}"
SESSION="${TMUX_SESSION:-sc2_v8_label_$(basename "${DATA_DIR:-run}")}"
DATA_DIR="${DATA_DIR:-bo_collection_runs/2026-06-27_terran_4bots_3maps_macro}"
OUTPUT="${OUTPUT:-sft_pipeline_outputs/$(basename "$DATA_DIR")/v8_steps}"
MODEL_KEY="${MODEL_KEY:-kimi-k2.5}"
WORKERS="${WORKERS:-8}"
MAX_CALLS_PER_MINUTE="${MAX_CALLS_PER_MINUTE:-60}"
MAX_LABEL_RETRIES="${MAX_LABEL_RETRIES:-5}"
SEQUENCE_ORDER="${SEQUENCE_ORDER:-diverse-hard-first}"
MAPS="${MAPS:-KairosJunctionLE AutomatonLE AbyssalReefLE}"

DATA_ABS="${ROOT}/${DATA_DIR#${ROOT}/}"
OUTPUT_ABS="${ROOT}/${OUTPUT#${ROOT}/}"
MASTER_LOG="${DATA_ABS}/master_run.log"
PIPELINE_LOG="${OUTPUT_ABS}/pipeline_run.log"
OBS_QA="${ROOT}/sft_pipeline_outputs/$(basename "$DATA_DIR")/obs_qa.json"

wait_for_map_collection() {
  local map_name="$1"
  local map_dir="${DATA_ABS}/${map_name}"
  echo "Waiting for map collection: ${map_name} ..."
  while true; do
    if [ -f "${MASTER_LOG}" ] && grep -q "Finished map: ${map_name}" "${MASTER_LOG}" 2>/dev/null; then
      echo "Map ${map_name} collection finished."
      return 0
    fi
    if [ -f "${map_dir}/summary.json" ]; then
      local seq_count
      seq_count=$(find "${map_dir}" -path '*/sequences/*.json' 2>/dev/null | wc -l)
      if [ "${seq_count}" -ge 1 ] && ! pgrep -f "collect_terran_bo.py.*${map_name}" >/dev/null 2>&1; then
        echo "Map ${map_name} summary present and collector idle (sequences=${seq_count})."
        return 0
      fi
    fi
    local seq_count
    seq_count=$(find "${map_dir}" -path '*/sequences/*.json' 2>/dev/null | wc -l)
    echo "[$(date -Iseconds)] ${map_name} collecting... sequences=${seq_count}"
    sleep 60
  done
}

if [ "${1:-}" = "__wait_map__" ]; then
  wait_for_map_collection "${2:?map name required}"
  exit 0
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux 会话已存在: $SESSION"
  echo "  附加: tmux attach -t $SESSION"
  exit 1
fi

mkdir -p "$OUTPUT_ABS"

RUN_CMD="source \"\$HOME/miniconda3/etc/profile.d/conda.sh\" && \
conda activate ${CONDA_ENV} && \
export PYTHONPATH=${ROOT}/python-sc2:\${PYTHONPATH:-} && \
export PYTHONUTF8=1 && \
export PYTHONIOENCODING=utf-8 && \
cd \"${ROOT}\" && \
exec > >(tee -a \"${PIPELINE_LOG}\") 2>&1 && \
echo \"========== v8 label pipeline start \$(date -Iseconds) ==========\" && \
echo \"DATA_DIR=${DATA_ABS}\" && \
echo \"OUTPUT=${OUTPUT_ABS}\" && \
echo \"MODEL_KEY=${MODEL_KEY} WORKERS=${WORKERS} MAX_CALLS_PER_MINUTE=${MAX_CALLS_PER_MINUTE}\" && \
echo \"SEQUENCE_ORDER=${SEQUENCE_ORDER}\" && \
echo \"MAPS=${MAPS}\" && \
for MAP in ${MAPS}; do \
  echo \"========== wait map: \${MAP} ==========\" && \
  DATA_DIR=\"${DATA_DIR}\" bash \"${ROOT}/tools/run_v8_label_pipeline.sh\" __wait_map__ \"\${MAP}\" && \
  echo \"========== Obs QA (\${MAP}) ==========\" && \
  python -m sft_pipeline.collect.validate_obs \
    --run \"${DATA_ABS}/\${MAP}\" \
    --output \"${OBS_QA%.json}_\${MAP}.json\" && \
  echo \"========== v8 labeling map \${MAP} (attempt 1/${MAX_LABEL_RETRIES}) ==========\" && \
  python -m sft_pipeline.label_steps.build_v8_steps \
    --data-dir \"${DATA_ABS}/\${MAP}\" \
    --output \"${OUTPUT_ABS}\" \
    --map \"\${MAP}\" \
    \$([ \"\${MAP}\" != \"KairosJunctionLE\" ] && echo --merge-existing) \
    --model-key \"${MODEL_KEY}\" \
    --workers ${WORKERS} \
    --max-calls-per-minute ${MAX_CALLS_PER_MINUTE} \
    --sequence-order ${SEQUENCE_ORDER} \
    2>&1 | tee -a \"${OUTPUT_ABS}/label_run_\${MAP}.log\"; \
done && \
attempt=2 && \
while [ \"\$attempt\" -le ${MAX_LABEL_RETRIES} ]; do \
  echo \"========== v8 QA check before retry ==========\" && \
  if python -m sft_pipeline.label_steps.validate_v8_steps \
    --data-dir \"${DATA_ABS}\" \
    --output \"${OUTPUT_ABS}\" \
    --report \"${OUTPUT_ABS}/v8_qa.json\" \
    --strict; then \
    echo \"v8 QA passed on attempt \$((attempt - 1)).\"; \
    break; \
  fi; \
  echo \"========== v8 retry labeling (attempt \$attempt/${MAX_LABEL_RETRIES}) ==========\" && \
  for MAP in ${MAPS}; do \
    python -m sft_pipeline.label_steps.build_v8_steps \
      --data-dir \"${DATA_ABS}/\${MAP}\" \
      --output \"${OUTPUT_ABS}\" \
      --map \"\${MAP}\" \
      --merge-existing \
      --model-key \"${MODEL_KEY}\" \
      --workers ${WORKERS} \
      --max-calls-per-minute ${MAX_CALLS_PER_MINUTE} \
      --sequence-order ${SEQUENCE_ORDER} \
      --skip-existing \
      2>&1 | tee -a \"${OUTPUT_ABS}/retry_failed_run.log\"; \
  done && \
  attempt=\$((attempt + 1)); \
done && \
echo \"========== final v8 QA ==========\" && \
python -m sft_pipeline.label_steps.validate_v8_steps \
  --data-dir \"${DATA_ABS}\" \
  --output \"${OUTPUT_ABS}\" \
  --report \"${OUTPUT_ABS}/v8_qa.json\" \
  --strict && \
echo \"========== v8 label pipeline done \$(date -Iseconds) ==========\""

tmux new-session -d -s "$SESSION" -c "$ROOT" bash -lc "$RUN_CMD"

echo "已在 tmux 后台启动 v8 标注流水线"
echo "  会话: tmux attach -t $SESSION"
echo "  输出: $OUTPUT_ABS"
echo "  日志: $PIPELINE_LOG"
echo "  地图顺序: ${MAPS}"
echo "  排序: ${SEQUENCE_ORDER}, workers=${WORKERS}, max=${MAX_CALLS_PER_MINUTE}/min"
