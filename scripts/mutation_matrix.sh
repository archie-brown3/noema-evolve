#!/usr/bin/env bash
# 0188 Stage 5 — two-way mutation matrix driver.
#
# One baseline run (NOEMA_MUTANT=none) followed by one full-collection run per
# mutant. Never -x: the matrix needs EVERY test's outcome under each mutant.
# A mutant is EXPECTED to redden the suite, so a non-zero exit never aborts.
#
# Usage: scripts/mutation_matrix.sh [out.jsonl] [mutant-id ...]
set -u

# Respect caller override (PY), prefer repo venv if present, else resolve active python3/sys.executable
if [ -n "${PY:-}" ]; then
  : # PY provided by caller
elif [ -x "/root/noema-evolve/.venv/bin/python3" ]; then
  PY="/root/noema-evolve/.venv/bin/python3"
else
  if command -v python3 >/dev/null 2>&1; then
    PY=$(python3 -c 'import sys; print(sys.executable)')
  elif command -v python >/dev/null 2>&1; then
    PY=$(python -c 'import sys; print(sys.executable)')
  else
    PY=python3
  fi
fi
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1

# 0188 Stage 7: the matrix's verdicts are only as reproducible as its runs.
# Donor test_sample_from_island_ratios.py:186 fails ~1 run in 25 under hash
# randomisation (upstream's weighted sampler iterates a SET, so a fixed RNG draw
# lands on a different program each process — measured, see the note in
# tests/test_noema_islands_adapter_fidelity_spec.py). A flaky baseline row would
# read as "this mutant was killed" for whichever mutant happened to draw it.
export PYTHONHASHSEED=0

OUT=${1:-artifacts/mutation/runs.jsonl}
shift || true
mkdir -p "$(dirname "$OUT")"
OUT=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")
export NOEMA_MUTANT_LOG="$OUT"

if [ "$#" -gt 0 ]; then
  IDS="$*"
  echo "# partial run: $# mutant(s), appending to $OUT"
else
  : > "$OUT"
  IDS="none $("$PY" -c 'from tests.mutation.mutants import MUTANTS; print("\n".join(MUTANTS))')"
fi

# Scope A (matrix collection) = gate 2 minus the islands-adapter ignore. See the
# stage note's "Matrix test population" section.
for M in $IDS; do
  echo "=== $M ==="
  NOEMA_MUTANT="$M" timeout 900 "$PY" -m pytest tests/ -q -p tests.mutation.plugin \
    --ignore=tests/upstream \
    --ignore=tests/test_noema_evaluator_adapter_fidelity_spec.py \
    2>&1 | tail -1
  rc=${PIPESTATUS[0]}
  [ "$rc" = 124 ] && echo "!!! TIMEOUT: $M — rows for this mutant are incomplete"
done
