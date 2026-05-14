#!/usr/bin/env bash
#
# 批量并发运行 run_vs_ai.py（引擎脚本；推荐用 run_vs_ai_batch_env.sh 集中改配置）。
# 用法:
#   ./run_vs_ai_batch.sh <总局数> <并发数> [fg|tmux]
#
#   fg   — 当前 shell 内用 xargs -P 控制并发（默认，需 GNU xargs）
#   tmux — 新建 tmux 会话，每个窗口一个 worker（按 stride 分发局数）
#
# 单局记录: <RECORD_ROOT>/<BATCH_NAME>/<match_id>/...
# 批控制台日志: <RECORD_ROOT>/_batch_logs/<BATCH_NAME>/
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

PYTHON="${PYTHON:-python3}"
RUN_SCRIPT="${RUN_SCRIPT:-run_vs_ai.py}"

# =============================================================================
# 对局 / 对手 / 地图（按需修改或通过环境变量覆盖）
# =============================================================================
MY_BOT_NAME="${MY_BOT_NAME:-universal_llm}"
MAP_NAME="${MAP_NAME:-KairosJunctionLE}"
REAL_TIME="${REAL_TIME:-0}" # 设为 1 启用 --real-time

ENEMY_RACE="${ENEMY_RACE:-terran}"
ENEMY_DIFFICULTY="${ENEMY_DIFFICULTY:-hard}"
ENEMY_BUILD="${ENEMY_BUILD:-macro}"

BOT_INSTRUCT="${BOT_INSTRUCT:-打一波 以 大和为主的攻击}"
BOT_RACE="${BOT_RACE:-terran}"

# =============================================================================
# LLM 三层模型（config.json 中的 key）
# =============================================================================
TOP_MODEL="${TOP_MODEL:-DeepSeek-V4-pro-reasoning}"
MID_MODEL="${MID_MODEL:-DeepSeek-V4-pro-reasoning}"
DOWN_MODEL="${DOWN_MODEL:-DeepSeek-V4-flash}"

# =============================================================================
# 批次目录名：留空则根据上方变量自动生成
# =============================================================================
BATCH_NAME="${BATCH_NAME:-}"

slug_part() {
  local s="${1:-}"
  echo "$s" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]\+/_/g' | cut -c1-48
}

usage() {
  echo "用法: $0 <总局数> <并发数> [fg|tmux]" >&2
  echo "  例: $0 12 4 fg      # 共 12 局，最多同时 4 局" >&2
  echo "  例: $0 12 4 tmux   # tmux 每窗口一个 worker" >&2
  exit 1
}

write_batch_env_file() {
  local f="$LOG_DIR/batch_env_$$.sh"
  umask 077
  {
    printf '%s\n' "ROOT=$(printf '%q' "$ROOT")"
    printf '%s\n' "PYTHON=$(printf '%q' "$PYTHON")"
    printf '%s\n' "RUN_SCRIPT=$(printf '%q' "$RUN_SCRIPT")"
    printf '%s\n' "MY_BOT_NAME=$(printf '%q' "$MY_BOT_NAME")"
    printf '%s\n' "MAP_NAME=$(printf '%q' "$MAP_NAME")"
    printf '%s\n' "REAL_TIME=$(printf '%q' "$REAL_TIME")"
    printf '%s\n' "ENEMY_RACE=$(printf '%q' "$ENEMY_RACE")"
    printf '%s\n' "ENEMY_DIFFICULTY=$(printf '%q' "$ENEMY_DIFFICULTY")"
    printf '%s\n' "ENEMY_BUILD=$(printf '%q' "$ENEMY_BUILD")"
    printf '%s\n' "BOT_INSTRUCT=$(printf '%q' "$BOT_INSTRUCT")"
    printf '%s\n' "BOT_RACE=$(printf '%q' "$BOT_RACE")"
    printf '%s\n' "TOP_MODEL=$(printf '%q' "$TOP_MODEL")"
    printf '%s\n' "MID_MODEL=$(printf '%q' "$MID_MODEL")"
    printf '%s\n' "DOWN_MODEL=$(printf '%q' "$DOWN_MODEL")"
    printf '%s\n' "BATCH_NAME=$(printf '%q' "$BATCH_NAME")"
    printf '%s\n' "RECORD_ROOT=$(printf '%q' "$RECORD_ROOT")"
    printf '%s\n' "LOG_DIR=$(printf '%q' "$LOG_DIR")"
  } >"$f"
  printf '%s' "$f"
}

# ----- tmux / xargs worker：source env 后 bash 本脚本 worker <id> -----
if [[ "${1:-}" == "worker" ]]; then
  WID="${2:-0}"
  BATCH_TOTAL="${BATCH_TOTAL:?缺少 BATCH_TOTAL}"
  BATCH_CONC="${BATCH_CONC:?缺少 BATCH_CONC}"
  LOG_DIR="${LOG_DIR:?缺少 LOG_DIR}"
  # shellcheck source=/dev/null
  source "${BATCH_ENV_FILE:?缺少 BATCH_ENV_FILE}"
  worker_loop() {
    local wid="$1" total="$2" conc="$3" logdir="$4" i
    for ((i = wid; i < total; i += conc)); do
      echo "[worker $wid] 开始 run_index=$i" | tee -a "$logdir/worker_${wid}.log"
      set +e
      run_one_match "$i" >>"$logdir/worker_${wid}.log" 2>&1
      ec=$?
      set -e
      echo "[worker $wid] 结束 run_index=$i exit=$ec" | tee -a "$logdir/worker_${wid}.log"
    done
  }
  run_one_match() {
    local idx="$1"
    local rt=()
    [[ "${REAL_TIME:-0}" == "1" ]] && rt=(--real-time)
    "$PYTHON" "$RUN_SCRIPT" \
      --my-bot-name "$MY_BOT_NAME" \
      --map-name "$MAP_NAME" \
      "${rt[@]}" \
      --enemy-race "$ENEMY_RACE" \
      --enemy-difficulty "$ENEMY_DIFFICULTY" \
      --enemy-build "$ENEMY_BUILD" \
      --bot-instruct "$BOT_INSTRUCT" \
      --bot-race "$BOT_RACE" \
      --top-model "$TOP_MODEL" \
      --mid-model "$MID_MODEL" \
      --down-model "$DOWN_MODEL" \
      --batch-name "$BATCH_NAME" \
      --run-index "$idx" \
      --output-base-dir "$RECORD_ROOT" \
      --skip-version-update
  }
  worker_loop "$WID" "$BATCH_TOTAL" "$BATCH_CONC" "$LOG_DIR"
  exit 0
fi

if [[ $# -lt 2 ]]; then
  usage
fi

TOTAL="$1"
CONCURRENCY="$2"
MODE="${3:-fg}"

if ! [[ "$TOTAL" =~ ^[0-9]+$ ]] || ! [[ "$CONCURRENCY" =~ ^[0-9]+$ ]]; then
  echo "总局数与并发数必须为非负整数" >&2
  exit 1
fi
if [[ "$TOTAL" -lt 1 ]]; then
  echo "总局数至少为 1" >&2
  exit 1
fi
if [[ "$CONCURRENCY" -lt 1 ]]; then
  echo "并发数至少为 1" >&2
  exit 1
fi

if [[ -z "$BATCH_NAME" ]]; then
  TS="$(date +%Y%m%d_%H%M%S)"
  BATCH_NAME="batch_${TS}_$(slug_part "$MAP_NAME")_$(slug_part "$BOT_RACE")v$(slug_part "$ENEMY_RACE")_$(slug_part "$ENEMY_DIFFICULTY")_$(slug_part "$TOP_MODEL")_$(slug_part "$MID_MODEL")_$(slug_part "$DOWN_MODEL")"
fi

RECORD_ROOT="${RECORD_ROOT:-./game_records}"
LOG_DIR="${LOG_DIR:-$RECORD_ROOT/_batch_logs/$BATCH_NAME}"
mkdir -p "$LOG_DIR"

BATCH_ENV_FILE="$(write_batch_env_file)"

run_one_match() {
  # shellcheck source=/dev/null
  source "$BATCH_ENV_FILE"
  local idx="$1"
  local rt=()
  [[ "$REAL_TIME" == "1" ]] && rt=(--real-time)
  "$PYTHON" "$RUN_SCRIPT" \
    --my-bot-name "$MY_BOT_NAME" \
    --map-name "$MAP_NAME" \
    "${rt[@]}" \
    --enemy-race "$ENEMY_RACE" \
    --enemy-difficulty "$ENEMY_DIFFICULTY" \
    --enemy-build "$ENEMY_BUILD" \
    --bot-instruct "$BOT_INSTRUCT" \
    --bot-race "$BOT_RACE" \
    --top-model "$TOP_MODEL" \
    --mid-model "$MID_MODEL" \
    --down-model "$DOWN_MODEL" \
    --batch-name "$BATCH_NAME" \
    --run-index "$idx" \
    --output-base-dir "$RECORD_ROOT" \
    --skip-version-update
}

echo "批次: $BATCH_NAME"
echo "总局数: $TOTAL  并发: $CONCURRENCY  模式: $MODE"
echo "单局记录: $RECORD_ROOT/$BATCH_NAME/<match_id>/"
echo "批控制台日志: $LOG_DIR"

$PYTHON -c "import sys; sys.path.insert(0, '.'); from version import update_version_txt; update_version_txt()" || true

run_fg_xargs() {
  seq 0 $((TOTAL - 1)) | xargs -P "$CONCURRENCY" -I{} bash -c '
    set -euo pipefail
    # shellcheck source=/dev/null
    source "$1"
    cd "$ROOT" || exit 1
    idx="$2"
    echo "[fg] 开始 run_index=$idx" | tee -a "$LOG_DIR/fg_run_${idx}.log"
    set +e
    rt=()
    [[ "$REAL_TIME" == "1" ]] && rt=(--real-time)
    "$PYTHON" "$RUN_SCRIPT" \
      --my-bot-name "$MY_BOT_NAME" \
      --map-name "$MAP_NAME" \
      "${rt[@]}" \
      --enemy-race "$ENEMY_RACE" \
      --enemy-difficulty "$ENEMY_DIFFICULTY" \
      --enemy-build "$ENEMY_BUILD" \
      --bot-instruct "$BOT_INSTRUCT" \
      --bot-race "$BOT_RACE" \
      --top-model "$TOP_MODEL" \
      --mid-model "$MID_MODEL" \
      --down-model "$DOWN_MODEL" \
      --batch-name "$BATCH_NAME" \
      --run-index "$idx" \
      --output-base-dir "$RECORD_ROOT" \
      --skip-version-update \
      >>"$LOG_DIR/fg_run_${idx}.log" 2>&1
    ec=$?
    set -e
    echo "[fg] 结束 run_index=$idx exit=$ec" | tee -a "$LOG_DIR/fg_run_${idx}.log"
    exit "$ec"
  ' _ "$BATCH_ENV_FILE" {}
}

run_fg_fallback_jobs() {
  local i
  for ((i = 0; i < TOTAL; i++)); do
    while ((($(jobs -r | wc -l) + 0) >= CONCURRENCY)); do
      wait || true
    done
    (
      echo "[fg] 开始 run_index=$i" | tee -a "$LOG_DIR/fg_run_${i}.log"
      set +e
      run_one_match "$i" >>"$LOG_DIR/fg_run_${i}.log" 2>&1
      ec=$?
      set -e
      echo "[fg] 结束 run_index=$i exit=$ec" | tee -a "$LOG_DIR/fg_run_${i}.log"
    ) &
  done
  wait || true
}

run_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "未找到 tmux，请安装或改用 fg 模式" >&2
    exit 1
  fi
  local sess
  sess="sc2_$(slug_part "$BATCH_NAME")_${$}"
  sess="$(echo "$sess" | cut -c1-56)"
  tmux has-session -t "$sess" 2>/dev/null && tmux kill-session -t "$sess"

  local w bf qbf qroot qpy
  bf=$(printf '%q' "$BATCH_ENV_FILE")
  qroot=$(printf '%q' "$ROOT")
  qpy=$(printf '%q' "$PYTHON")
  qscript=$(printf '%q' "$0")

  for ((w = 0; w < CONCURRENCY; w++)); do
    local inner
    inner="export BATCH_ENV_FILE=$bf BATCH_TOTAL=$TOTAL BATCH_CONC=$CONCURRENCY LOG_DIR=$(printf '%q' "$LOG_DIR"); cd $qroot && $qpy $qscript worker $w; echo worker_${w}_done; read -r _"
    if [[ "$w" -eq 0 ]]; then
      tmux new-session -d -s "$sess" -n "w${w}" bash -lc "$inner"
    else
      tmux new-window -t "$sess" -n "w${w}" bash -lc "$inner"
    fi
  done
  echo "tmux 会话: $sess （每窗口一个 worker: w0 .. w$((CONCURRENCY - 1))）"
  echo "附加: tmux attach -t $(printf '%q' "$sess")"
}

case "$MODE" in
  fg)
    if seq 0 0 2>/dev/null | xargs -P 2 true 2>/dev/null; then
      run_fg_xargs
    else
      echo "当前 xargs 不支持 -P，改用 bash 后台 job 池" >&2
      run_fg_fallback_jobs
    fi
    ;;
  tmux)
    run_tmux
    ;;
  *)
    echo "未知模式: $MODE （使用 fg 或 tmux）" >&2
    exit 1
    ;;
esac
