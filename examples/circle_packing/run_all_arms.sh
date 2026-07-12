#!/bin/bash
# All-arms enriched-prompt sweep for circle_packing (task 0070).
#
# Runs null, hifo, pes-custom, pes-faithful back-to-back against one
# inference node, same seed/model/budget, changing only coordination.module
# and each arm's canonical retry rule. One process per arm (sequential,
# isolated) so a crash in one arm doesn't kill the rest.
#
# Usage:
#   BUDGET=500000 API_BASE=http://100.95.96.20:8080/v1 \
#     nohup bash examples/circle_packing/run_all_arms.sh \
#     > examples/circle_packing/runs/sweep-enriched.log 2>&1 &
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BUDGET="${BUDGET:-500000}"
API_BASE="${API_BASE:?set API_BASE, e.g. http://100.95.96.20:8080/v1}"
MODEL="${MODEL:-Qwen3-30B-A3B-Instruct-2507-Q8_0}"
SEED="${SEED:-42}"
RUNS_DIR="$SCRIPT_DIR/runs"
mkdir -p "$RUNS_DIR"

# Enriched prompt config (Decision #11 baseline gate): un-starve the mutation
# prompt relative to the config that produced the 0.547 starved-null score.
COMMON_ARGS=(
  --api-base "$API_BASE"
  --model "$MODEL"
  --seed "$SEED"
  --budget-tokens "$BUDGET"
  --iterations 2000
  --num-inspirations 3
  --num-top-programs 3
  --include-artifacts
)

# arm:retry_flags — retry is arm-defining, not a shared knob (0070 config table).
ARMS=(
  "null:"
  "hifo:"
  "pes-custom:--retry-enabled --retry-cap 2 --retry-on failure"
  "pes-faithful:--retry-enabled --retry-cap 2 --retry-on non_improvement"
)

declare -A RESULTS

for entry in "${ARMS[@]}"; do
  arm="${entry%%:*}"
  retry_args="${entry#*:}"
  out_dir="$RUNS_DIR/${arm}-30b-enriched-s${SEED}"
  log_file="$out_dir.log"
  mkdir -p "$out_dir"

  echo "=== [$arm] starting $(date -u +%FT%TZ) -> $out_dir ==="
  # shellcheck disable=SC2086
  "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/run_noema_arm.py" \
    --arm "$arm" \
    --output-dir "$out_dir" \
    "${COMMON_ARGS[@]}" \
    $retry_args \
    > "$log_file" 2>&1
  status=$?

  if [ $status -ne 0 ]; then
    echo "=== [$arm] FAILED (exit $status), see $log_file — continuing to next arm ==="
    RESULTS["$arm"]="FAILED (exit $status)"
    continue
  fi

  best_line="$(grep "^BEST:" "$log_file" | tail -1)"
  RESULTS["$arm"]="${best_line:-no BEST line found}"
  echo "=== [$arm] done $(date -u +%FT%TZ): ${RESULTS[$arm]} ==="
done

echo
echo "=== Sweep summary (seed=$SEED, budget=$BUDGET, model=$MODEL) ==="
for entry in "${ARMS[@]}"; do
  arm="${entry%%:*}"
  printf '%-14s %s\n' "$arm" "${RESULTS[$arm]}"
done
