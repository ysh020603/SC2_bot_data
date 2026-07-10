#!/usr/bin/env bash
# 逐个 bot 跑 1 局冒烟测试（不干扰主采集任务的端口段）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "${CONDA_ENV:-sharpy-sc2}"
export SC2PATH=/data2/SC2/StarCraftII/
export PYTHONPATH="${ROOT}/python-sc2:${PYTHONPATH:-}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

OUTPUT="${OUTPUT:-bo_collection_runs/bot_smoke_2026-07-06}"
MAP="${MAP:-KairosJunctionLE}"
PORT_BASE="${PORT_BASE:-36000}"
LOG="${OUTPUT}/smoke_test.log"

mkdir -p "$OUTPUT"

BOTS=(
  banshees battle_cruisers bio cyclones marine_rush two_base_tanks
  raven_screams yamato_rust_fleet rusty_bio_mines blueflame_locks stim_rush_relay two_base_matrix_tanks
)

echo "=== Bot smoke test start $(date -Iseconds) ===" | tee "$LOG"
echo "Output: $OUTPUT" | tee -a "$LOG"
echo "Map: $MAP, matchup: zerg veryeasy macro, workers=1" | tee -a "$LOG"

port="$PORT_BASE"
fail=0
for bot in "${BOTS[@]}"; do
  echo "" | tee -a "$LOG"
  echo ">>> Testing bot: $bot (port-offset $port)" | tee -a "$LOG"
  if python tools/collect_terran_bo.py \
    --output "$OUTPUT" \
    --map "$MAP" \
    --bots "$bot" \
    --races zerg \
    --difficulties veryeasy \
    --enemy-build macro \
    --workers 1 \
    --port-offset "$port" \
    2>&1 | tee -a "$LOG" "${OUTPUT}/${bot}_smoke.log"; then
    result=$(python3 -c "
import json, sys
from pathlib import Path
p = Path('$OUTPUT') / '$bot' / 'results.json'
if not p.exists():
    print('MISSING_RESULTS'); sys.exit(1)
d = json.loads(p.read_text())
r = d['matches'][0] if d.get('matches') else d['results'][0]
status = r.get('status','?')
victory = r.get('victory', False)
result = r.get('result') or r.get('error','?')
seq = r.get('sequence_file','')
print(f'{status}|{victory}|{result}|{bool(seq)}')
")
    IFS='|' read -r status victory result has_seq <<< "$result"
    if [[ "$status" == "ok" && "$has_seq" == "True" ]]; then
      echo "    OK: $bot -> $result (sequence=yes)" | tee -a "$LOG"
    else
      echo "    FAIL: $bot status=$status result=$result seq=$has_seq" | tee -a "$LOG"
      fail=$((fail + 1))
    fi
  else
    echo "    FAIL: $bot (script exit $?)" | tee -a "$LOG"
    fail=$((fail + 1))
  fi
  port=$((port + 8))
done

echo "" | tee -a "$LOG"
echo "=== Bot smoke test done $(date -Iseconds), failures=$fail ===" | tee -a "$LOG"
exit "$fail"
