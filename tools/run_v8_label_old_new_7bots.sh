#!/usr/bin/env bash
# NEW 7 bots 完成后继续 OLD 7 bots 的 v8 step 标注（Kimi nothinking）。
# 用法:
#   bash tools/run_v8_label_old_new_7bots.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="${CONDA_ENV:-sharpy-sc2}"
SESSION="${TMUX_SESSION:-sc2_v8_label_old_new_7bots_20260705}"
MODEL_KEY="${MODEL_KEY:-kimi-k2.5}"
WORKERS="${WORKERS:-5}"
MAX_CALLS_PER_MINUTE="${MAX_CALLS_PER_MINUTE:-60}"
SEQUENCE_ORDER="${SEQUENCE_ORDER:-diverse-hard-first}"
MAPS=(KairosJunctionLE AutomatonLE AbyssalReefLE)

NEW_DATA="bo_collection_runs/2026-07-05_terran_new7bots_3maps_macro"
OLD_DATA="bo_collection_runs/2026-07-05_terran_old7bots_3maps_macro"
NEW_OUTPUT="sft_pipeline_outputs/2026-07-05_terran_new7bots_3maps_macro/v8_steps"
OLD_OUTPUT="sft_pipeline_outputs/2026-07-05_terran_old7bots_3maps_macro/v8_steps"
MASTER_LOG="${ROOT}/sft_pipeline_outputs/2026-07-05_v8_label_old_new/master_run.log"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux 会话已存在: $SESSION"
  echo "  附加: tmux attach -t $SESSION"
  exit 1
fi

if pgrep -f "sft_pipeline.label_steps.build_v8_steps" >/dev/null; then
  echo "已有 build_v8_steps 在运行，请先确认后再启动。"
  pgrep -af "sft_pipeline.label_steps.build_v8_steps" || true
  exit 1
fi

mkdir -p "$(dirname "$MASTER_LOG")" "${ROOT}/${NEW_OUTPUT}" "${ROOT}/${OLD_OUTPUT}"

RUN_CMD="source \"\$HOME/miniconda3/etc/profile.d/conda.sh\" && \
conda activate ${CONDA_ENV} && \
export PYTHONPATH=${ROOT}/python-sc2:\$PYTHONPATH && \
export PYTHONUTF8=1 && \
export PYTHONIOENCODING=utf-8 && \
cd \"${ROOT}\" && \
exec > >(tee -a \"${MASTER_LOG}\") 2>&1 && \
echo \"========== v8 OLD+NEW label start \$(date -Iseconds) ==========\" && \
echo \"MODEL_KEY=${MODEL_KEY} WORKERS=${WORKERS} MAX_CALLS_PER_MINUTE=${MAX_CALLS_PER_MINUTE}\" && \
for BATCH in NEW OLD; do \
  if [ \"\$BATCH\" = NEW ]; then \
    DATA_DIR=\"${NEW_DATA}\"; OUTPUT_DIR=\"${NEW_OUTPUT}\"; \
  else \
    DATA_DIR=\"${OLD_DATA}\"; OUTPUT_DIR=\"${OLD_OUTPUT}\"; \
  fi; \
  DATA_ABS=\"${ROOT}/\${DATA_DIR}\"; \
  OUTPUT_ABS=\"${ROOT}/\${OUTPUT_DIR}\"; \
  OBS_ROOT=\"${ROOT}/sft_pipeline_outputs/\$(basename \"\${DATA_DIR}\")\"; \
  echo \"========== \${BATCH} batch: \${DATA_DIR} ==========\"; \
  for MAP in ${MAPS[*]}; do \
    echo \"========== Obs QA (\${BATCH}/\${MAP}) ==========\"; \
    python -m sft_pipeline.collect.validate_obs \
      --run \"\${DATA_ABS}/\${MAP}\" \
      --output \"\${OBS_ROOT}/obs_qa_\${MAP}.json\"; \
    echo \"========== v8 labeling \${BATCH}/\${MAP} ==========\"; \
    MERGE_ARGS=\"\"; \
    if [ \"\${MAP}\" != KairosJunctionLE ]; then MERGE_ARGS=\"--merge-existing\"; fi; \
    python -m sft_pipeline.label_steps.build_v8_steps \
      --data-dir \"\${DATA_ABS}/\${MAP}\" \
      --output \"\${OUTPUT_ABS}\" \
      --map \"\${MAP}\" \
      \$MERGE_ARGS \
      --model-key \"${MODEL_KEY}\" \
      --workers ${WORKERS} \
      --max-calls-per-minute ${MAX_CALLS_PER_MINUTE} \
      --sequence-order ${SEQUENCE_ORDER} \
      2>&1 | tee -a \"\${OUTPUT_ABS}/label_run_\${MAP}.log\"; \
  done; \
  echo \"========== v8 QA (\${BATCH}) ==========\"; \
  python -m sft_pipeline.label_steps.validate_v8_steps \
    --data-dir \"\${DATA_ABS}\" \
    --output \"\${OUTPUT_ABS}\" \
    --report \"\${OUTPUT_ABS}/v8_qa.json\" \
    --strict; \
  echo \"========== \${BATCH} DONE at \$(date -Iseconds) ==========\"; \
done && \
echo \"========== ALL BATCHES DONE \$(date -Iseconds) ==========\""

tmux new-session -d -s "$SESSION" -c "$ROOT" bash -lc "$RUN_CMD"

echo "已在 tmux 后台启动 v8 标注（先 NEW 后 OLD）"
echo "  会话: tmux attach -t $SESSION"
echo "  主日志: $MASTER_LOG"
echo "  NEW 输出: ${ROOT}/${NEW_OUTPUT}"
echo "  OLD 输出: ${ROOT}/${OLD_OUTPUT}"
echo "  模型: ${MODEL_KEY} (nothinking), workers=${WORKERS}, max=${MAX_CALLS_PER_MINUTE}/min"
