#!/bin/bash
# Task 0085 shakedown => The user's original query that started this task was:
#
# 0085 - Cloud Flash shakedown: 14-run mechanism-substrate debug matrix
#
# Goal: Gather live evidence on bugs and system determinism across every
# implemented arm before any study run, on a cloud model cheap enough that
# the whole matrix costs ~£1.
#
# 14-run mechanism-substrate debug matrix. All cells run concurrently against
# OpenRouter — no local hardware is blocked.
# Total worst-case spend: 7M tokens ≈ £1.51 (user cap £3–5).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNS_DIR="$SCRIPT_DIR/runs/0085"
mkdir -p "$RUNS_DIR"

set -a
source "$REPO_ROOT/.env"
set +a

COMMON=(
  --iterations 30 --budget-tokens 500000 --seed 42
  --temperature 0.0 --context-window-tokens 16384
  --api-base "https://openrouter.ai/api/v1"
  --api-key-env OPENROUTER_API_KEY
  --model "deepseek/deepseek-v4-flash"
  --num-inspirations 0 --num-top-programs 1
)

CELLS=(
  "null-islands-stock:null:islands:substrate_default"
  "null-tree-uct:null:tree:uct"
  "hifo-islands-stock:hifo:islands:stock_openevolve"
  "hifo-tree-uct:hifo:tree:uct"
  "pes-faithful-islands-stock:pes-faithful:islands:stock_openevolve"
  "pes-faithful-tree-uct:pes-faithful:tree:uct"
  "null-islands-boltzmann:null:islands:boltzmann"
)

echo "=== 0085 shakedown start $(date -u +%FT%TZ) ==="

PIDS=""
NAMES=""
for cell in "${CELLS[@]}"; do
  IFS=: read -r label arm substrate policy <<< "$cell"
  for rep in 1 2; do
    out_dir="$RUNS_DIR/$label-r$rep"
    log_file="$out_dir.log"
    mkdir -p "$out_dir"

    echo "  [$label-r$rep] launching"
    nohup "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/run_noema_arm.py" \
      --arm "$arm" --substrate "$substrate" --selection-policy "$policy" \
      --output-dir "$out_dir" \
      "${COMMON[@]}" \
      </dev/null > "$log_file" 2>&1 &
    PIDS="$PIDS $!"
    NAMES="$NAMES $label-r$rep"
  done
done

echo "=== 14 runs running; waiting ==="
FAILED=0
pids_arr=($PIDS)
names_arr=($NAMES)
for i in "${!pids_arr[@]}"; do
  pid="${pids_arr[$i]}"
  name="${names_arr[$i]}"
  if wait "$pid"; then
    echo "  [$name] OK $(date -u +%FT%TZ)"
  else
    echo "  [$name] FAILED (exit $?) $(date -u +%FT%TZ)"
    FAILED=1
  fi
done

echo ""
echo "=== summary ==="
for cell in "${CELLS[@]}"; do
  IFS=: read -r label _ _ _ <<< "$cell"
  for rep in 1 2; do
    best="$(grep '^BEST:' "$RUNS_DIR/$label-r$rep.log" 2>/dev/null | tail -1 || true)"
    printf '%-35s %s\n' "$label-r$rep" "${best:-<no BEST line — check log>}"
  done
done
echo "=== 0085 shakedown end $(date -u +%FT%TZ) ==="
exit $FAILED