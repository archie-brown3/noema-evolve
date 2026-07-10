#!/usr/bin/env bash
# Fixture-based test for loop/scripts/verify-run.sh. Run: bash tests/test_verify_run.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/loop/scripts/verify-run.sh"
FIX="$ROOT/tests/fixtures/verify_run"
fails=0

run() { bash "$SCRIPT" "$@" 2>&1; }

# name, expect_check ("" = expect exit 0 + 7 PASS lines), then --dir/--ticket args
cases=(
  "pass fixture (all 7 checks)|.|--dir|$FIX/pass|--ticket|$FIX/ticket_pass.md"
  "ledger-exact corruption|ledger-exact|--dir|$FIX/fail_ledger"
  "budget-respected corruption|budget-respected|--dir|$FIX/fail_budget"
  "island-distribution corruption|island-distribution|--dir|$FIX/fail_islands"
  "zero-context-overflow corruption|zero-context-overflow|--dir|$FIX/fail_contextoverflow"
  "prompt-identity corruption|prompt-identity|--dir|$FIX/fail_promptidentity_a|--ticket|$FIX/ticket_fail_promptidentity.md"
  "config-delta corruption|config-delta|--dir|$FIX/fail_configdelta_a|--ticket|$FIX/ticket_fail_configdelta.md"
  "seed-config-match corruption|seed-config-match|--dir|$FIX/fail_seedconfig|--ticket|$FIX/ticket_fail_seedconfig.md"
)

for c in "${cases[@]}"; do
  IFS='|' read -r name expect_check argstr <<<"$c"
  IFS='|' read -ra args <<<"$argstr"
  out=$(run "${args[@]}"); rc=$?
  if [ "$expect_check" = "." ]; then
    n=$(printf '%s\n' "$out" | grep -c '^PASS ')
    if [ "$rc" -eq 0 ] && [ "$n" -eq 7 ] && ! printf '%s\n' "$out" | grep -q '^FAIL '; then
      echo "ok $name"; continue
    fi
    echo "FAIL $name: expected exit 0 and 7 PASS lines, got rc=$rc n=$n; output:"$'\n'"$out"
  else
    if [ "$rc" -ne 0 ] && printf '%s\n' "$out" | grep -q "^FAIL $expect_check:"; then
      echo "ok $name"; continue
    fi
    echo "FAIL $name: expected non-zero exit with 'FAIL $expect_check:', got rc=$rc; output:"$'\n'"$out"
  fi
  fails=$((fails+1))
done

[ "$fails" -eq 0 ] && echo "all test_verify_run.sh checks passed" || echo "$fails test_verify_run.sh check(s) failed"
exit "$fails"
