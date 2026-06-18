#!/usr/bin/env bash
# run_vs_ai_batch.sh
# 批量并发引擎（接收 start_experiments.sh 传递的环境变量）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

PYTHON="${PYTHON:-python3}"
RUN_SCRIPT="${RUN_SCRIPT:-run_vs_ai.py}"

# 继承环境变量或使用默认值
MY_BOT_NAME="${MY_BOT_NAME:-universal_llm}"
MAP_NAME="${MAP_NAME:-KairosJunctionLE}"
REAL_TIME="${REAL_TIME:-0}"
ENEMY_RACE="${ENEMY_RACE:-terran}"
ENEMY_DIFFICULTY="${ENEMY_DIFFICULTY:-hard}"
ENEMY_BUILD="${ENEMY_BUILD:-macro}"
BOT_RACE="${BOT_RACE:-terran}"
NAMING_MODEL="${NAMING_MODEL:-DeepSeek-V4-flash}"
ORDERING_MODEL="${ORDERING_MODEL:-DeepSeek-V4-flash}"
EXECUTOR_MODEL="${EXECUTOR_MODEL:-DeepSeek-V4-flash}"
FORCE_STRATEGY="${FORCE_STRATEGY:-}"
BATCH_NAME="${BATCH_NAME:-}"

slug_part() {
  local s="${1:-}"
  echo "$s" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]\+/_/g' | cut -c1-48
}

usage() {
  echo "用法: $0 <总局数> <并发数> [fg|tmux]" >&2
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
    printf '%s\n' "BOT_RACE=$(printf '%q' "$BOT_RACE")"
    printf '%s\n' "NAMING_MODEL=$(printf '%q' "$NAMING_MODEL")"
    printf '%s\n' "ORDERING_MODEL=$(printf '%q' "$ORDERING_MODEL")"
    printf '%s\n' "EXECUTOR_MODEL=$(printf '%q' "$EXECUTOR_MODEL")"
    printf '%s\n' "FORCE_STRATEGY=$(printf '%q' "$FORCE_STRATEGY")"
    printf '%s\n' "BATCH_NAME=$(printf '%q' "$BATCH_NAME")"
    printf '%s\n' "RECORD_ROOT=$(printf '%q' "$RECORD_ROOT")"
    printf '%s\n' "LOG_DIR=$(printf '%q' "$LOG_DIR")"
  } >"$f"
  printf '%s' "$f"
}

# ----- tmux / xargs worker 的核心执行逻辑 -----
if [[ "${1:-}" == "worker" ]]; then
  WID="${2:-0}"
  BATCH_TOTAL="${BATCH_TOTAL:?缺少 BATCH_TOTAL}"
  BATCH_CONC="${BATCH_CONC:?缺少 BATCH_CONC}"
  LOG_DIR="${LOG_DIR:?缺少 LOG_DIR}"
  source "${BATCH_ENV_FILE:?缺少 BATCH_ENV_FILE}"
  
  worker_loop() {
    local wid="$1" total="$2" conc="$3" logdir="$4" i
    for ((i = wid; i < total; i += conc)); do
      echo "[Worker $wid] 开始运行 第 $i 局..." | tee -a "$logdir/worker_${wid}.log"
      set +e
      run_one_match "$i" >>"$logdir/worker_${wid}.log" 2>&1
      ec=$?
      set -e
      echo "[Worker $wid] 第 $i 局结束，退出码=$ec" | tee -a "$logdir/worker_${wid}.log"
    done
  }
  
  run_one_match() {
    local idx="$1"
    local rt=()
    [[ "${REAL_TIME:-0}" == "1" ]] && rt=(--real-time)

    local extra_flags=()
    if [[ -n "${FORCE_STRATEGY:-}" ]]; then
      extra_flags+=(--force-strategy "$FORCE_STRATEGY")
    fi

    "$PYTHON" "$RUN_SCRIPT" \
      --my-bot-name "$MY_BOT_NAME" \
      --map-name "$MAP_NAME" \
      "${rt[@]}" \
      --enemy-race "$ENEMY_RACE" \
      --enemy-difficulty "$ENEMY_DIFFICULTY" \
      --enemy-build "$ENEMY_BUILD" \
      --bot-race "$BOT_RACE" \
      --naming-model "$NAMING_MODEL" \
      --ordering-model "$ORDERING_MODEL" \
      --executor-model "$EXECUTOR_MODEL" \
      "${extra_flags[@]}" \
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

if [[ -z "$BATCH_NAME" ]]; then
  TS="$(date +%Y%m%d_%H%M)"
  BATCH_NAME="batch_${TS}_${MAP_NAME}_${BOT_RACE}V${ENEMY_RACE}_${ENEMY_DIFFICULTY}_$(slug_part "$NAMING_MODEL")_$(slug_part "$ORDERING_MODEL")"
fi

RECORD_ROOT="${RECORD_ROOT:-./game_records}"
LOG_DIR="${LOG_DIR:-$RECORD_ROOT/_batch_logs/$BATCH_NAME}"
mkdir -p "$LOG_DIR"

BATCH_ENV_FILE="$(write_batch_env_file)"

echo "=================================================="
echo " 批次文件夹 : $BATCH_NAME"
echo " 运行总数   : $TOTAL 局"
echo " 并发数量   : $CONCURRENCY"
echo " 运行模式   : $MODE"
echo " 单局录像   : $RECORD_ROOT/$BATCH_NAME/"
echo " 终端日志   : $LOG_DIR"
echo "=================================================="

$PYTHON -c "import sys; sys.path.insert(0, '.'); from version import update_version_txt; update_version_txt()" || true

run_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "未找到 tmux，请安装或改用 fg 模式" >&2
    exit 1
  fi
  local sess="sc2_batch_${$}"
  tmux has-session -t "$sess" 2>/dev/null && tmux kill-session -t "$sess"

  local w bf qroot qscript
  bf=$(printf '%q' "$BATCH_ENV_FILE")
  qroot=$(printf '%q' "$ROOT")
  qscript=$(printf '%q' "$0")

  for ((w = 0; w < CONCURRENCY; w++)); do
    local inner
    inner="export BATCH_ENV_FILE=$bf BATCH_TOTAL=$TOTAL BATCH_CONC=$CONCURRENCY LOG_DIR=$(printf '%q' "$LOG_DIR"); cd $qroot && bash $qscript worker $w; echo '[Worker $w] 全部任务完成。'; read -r _"
    
    if [[ "$w" -eq 0 ]]; then
      tmux new-session -d -s "$sess" -n "w${w}" bash -lc "$inner"
    else
      tmux new-window -t "$sess" -n "w${w}" bash -lc "$inner"
    fi
  done
  echo "=> Tmux 会话已创建: $sess (每窗口一个并发线程 w0 .. w$((CONCURRENCY - 1)))"
  echo "=> 可以通过以下命令查看运行状态: tmux attach -t $(printf '%q' "$sess")"
}

case "$MODE" in
  tmux) run_tmux ;;
  fg)   
    echo "使用后台 Jobs 模式..."
    for ((i = 0; i < TOTAL; i++)); do
      while ((($(jobs -r | wc -l) + 0) >= CONCURRENCY)); do wait -n || true; done
      ( source "$BATCH_ENV_FILE"; run_one_match "$i" >>"$LOG_DIR/fg_run_${i}.log" 2>&1 ) &
    done
    wait
    ;;
  *) echo "未知模式: $MODE" >&2; exit 1 ;;
esac
