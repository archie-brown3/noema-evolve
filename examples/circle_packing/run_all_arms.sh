#!/bin/bash
# All-arms enriched-prompt sweep for circle_packing (task 0070).
#
# Runs each arm on its OWN 3-node cluster, ALL AT THE SAME TIME.
#
# Why one cluster per arm: llama.cpp RPC is pipeline (layer-split) parallelism —
# layer N+1 needs layer N's output, so only one GPU computes at a time per
# request. A 3-node cluster therefore delivers roughly ONE GPU of compute, and
# batching more requests into it does not help (prefill is compute-saturated).
# The only way to get real parallel compute is independent pipelines. Measured
# 2026-07-13: three 8.9k-token prompts, one per cluster, finished in 203s wall
# vs ~570s sequential — a genuine 2.8x.
#
# hifo is NOT in this sweep (vault task 0072): three verified fidelity defects
# make its number uninterpretable — insight extraction is fed truncated code
# (changes_description is never set anywhere), the navigator cannot reach its
# exploitation regime, and its foresight regime has no operator scheduler to
# govern. null/pes-* are unaffected; all three defects are hifo-internal.
#
# Usage (from the VPS, which can reach all three clusters over tailscale):
#   BUDGET=1000000 bash examples/circle_packing/run_all_arms.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

BUDGET="${BUDGET:-1000000}"
MODEL="${MODEL:-Qwen3-30B-A3B-Instruct-2507-Q8_0}"
SEED="${SEED:-42}"
HOST="${HOST:-100.95.96.20}"
RUNS_DIR="$SCRIPT_DIR/runs"
mkdir -p "$RUNS_DIR"

# Enriched prompt (Decision #11 baseline gate): un-starve the mutation prompt
# relative to the config that produced the starved-null 0.547.
COMMON_ARGS=(
  --model "$MODEL"
  --seed "$SEED"
  --budget-tokens "$BUDGET"
  --iterations 2000
  --num-inspirations 3
  --num-top-programs 3
  --include-artifacts
)

# arm : api_port : retry_flags   (retry is arm-defining, not a shared knob)
ARMS=(
  "null:8080:"
  "pes-custom:8081:--retry-enabled --retry-cap 2 --retry-on failure"
  "pes-faithful:8082:--retry-enabled --retry-cap 2 --retry-on non_improvement"
)

echo "=== sweep start $(date -u +%FT%TZ) ==="
echo "    budget=$BUDGET/arm  seed=$SEED  model=$MODEL"
echo "    one arm per cluster, running concurrently"

# Preflight every endpoint before burning a night on a half-up cluster.
for entry in "${ARMS[@]}"; do
  IFS=: read -r arm port _ <<< "$entry"
  if ! curl -sf --max-time 10 "http://${HOST}:${port}/health" >/dev/null 2>&1; then
    echo "PREFLIGHT FAILED: no healthy API for $arm at ${HOST}:${port} — aborting."
    exit 1
  fi
  echo "    preflight ok: $arm -> ${HOST}:${port}"
done

declare -A PIDS
for entry in "${ARMS[@]}"; do
  IFS=: read -r arm port retry_args <<< "$entry"
  out_dir="$RUNS_DIR/${arm}-30b-enriched-s${SEED}"
  log_file="$out_dir.log"
  mkdir -p "$out_dir"

  echo "=== [$arm] launching on :$port -> $out_dir"
  # shellcheck disable=SC2086
  nohup "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/run_noema_arm.py" \
    --arm "$arm" \
    --output-dir "$out_dir" \
    --api-base "http://${HOST}:${port}/v1" \
    "${COMMON_ARGS[@]}" \
    $retry_args \
    </dev/null > "$log_file" 2>&1 &
  PIDS["$arm"]=$!
  echo "        pid ${PIDS[$arm]}"
done

echo ""
echo "=== all arms running; waiting for completion ==="
FAILED=0
for arm in "${!PIDS[@]}"; do
  if wait "${PIDS[$arm]}"; then
    echo "  [$arm] exited 0 at $(date -u +%FT%TZ)"
  else
    echo "  [$arm] FAILED (exit $?) at $(date -u +%FT%TZ)"
    FAILED=1
  fi
done

echo ""
echo "=== summary (seed=$SEED, budget=$BUDGET, model=$MODEL) ==="
for entry in "${ARMS[@]}"; do
  IFS=: read -r arm _ _ <<< "$entry"
  best="$(grep '^BEST:' "$RUNS_DIR/${arm}-30b-enriched-s${SEED}.log" 2>/dev/null | tail -1)"
  printf '%-14s %s\n' "$arm" "${best:-<no BEST line — check log>}"
done
echo "=== sweep end $(date -u +%FT%TZ) ==="
exit $FAILED
