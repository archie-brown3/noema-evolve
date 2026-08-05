#!/usr/bin/env bash
# 0188 Stage 5 — two-way mutation matrix driver.
#
# One baseline run (NOEMA_MUTANT=none) followed by one full-collection run per
# mutant. Never -x: the matrix needs EVERY test's outcome under each mutant.
# A mutant is EXPECTED to redden the suite, so a non-zero exit never aborts.
#
# Usage: scripts/mutation_matrix.sh [out.jsonl] [mutant-id ...]
set -u

PY=${PY:-/root/noema-evolve/.venv/bin/python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1

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
