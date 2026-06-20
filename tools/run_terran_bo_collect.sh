#!/usr/bin/env bash
# 在 tmux 后台批量采集 Terran dummy bot 的 BO 轨迹数据。
# 用法见 docs/collect_terran_bo.md

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="${CONDA_ENV:-SC2_0615}"
SESSION="${TMUX_SESSION:-sc2_terran_bo_collect}"
OUTPUT="${OUTPUT:-bo_collection_runs/$(date +%Y-%m-%d_%H_%M_%S)}"
MAP="${MAP:-KairosJunctionLE}"
WORKERS="${WORKERS:-15}"
PORT_OFFSET="${PORT_OFFSET:-25000}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux 会话已存在: $SESSION"
    echo "  附加: tmux attach -t $SESSION"
    exit 1
fi

if pgrep -f "tools/collect_terran_bo.py" >/dev/null; then
    echo "已有 collect_terran_bo.py 进程在运行，请先确认或停止后再启动。"
    pgrep -af "tools/collect_terran_bo.py" || true
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"
LOG="${OUTPUT}_run.log"

RUN_CMD="source \"\$HOME/miniconda3/etc/profile.d/conda.sh\" && \
conda activate ${CONDA_ENV} && \
cd \"${ROOT}\" && \
python tools/collect_terran_bo.py \
  --output \"${OUTPUT}\" \
  --map \"${MAP}\" \
  --workers ${WORKERS} \
  --port-offset ${PORT_OFFSET} \
  ${EXTRA_ARGS} \
  2>&1 | tee \"${LOG}\""

tmux new-session -d -s "$SESSION" -c "$ROOT" bash -lc "$RUN_CMD"

echo "已在 tmux 后台启动采集任务"
echo "  会话名: $SESSION"
echo "  输出目录: $OUTPUT"
echo "  日志: $LOG"
echo ""
echo "  查看: tmux attach -t $SESSION"
echo "  脱离: Ctrl+b 然后 d"
echo "  列表: tmux ls"
