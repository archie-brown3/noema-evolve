#!/usr/bin/env bash
# Post-run verification, spec/LIVE-RUNS.md §4: one function per bullet, exit
# non-zero on the first failure, read-only on the run dir.
# usage: verify-run.sh --dir <run-dir> [--ticket <ticket-file>]
#
# Reads <dir>/llm_calls.jsonl, <dir>/checkpoints/checkpoint_<N>/{noema_state.json
# (.ledger.total_budget_tokens/.spent_by_account), metadata.json (.islands)},
# <dir>/run.log (optional), <dir>/run_manifest.json (optional: .seed
# .config_sha256 .config{flat} .prompt_system, coordination suffix delimited
# by the literal marker "<<<COORD>>>").
#
# --ticket: markdown, YAML-ish frontmatter between the first two "---" lines:
# a top-level "config_sha256: <sha>" scalar and a "runs:" list of flow maps
# "- {arm: <a>, dir: <path>, seed: <n>}". Paired-arm checks (prompt-identity,
# config-delta) activate only when the ticket lists exactly two runs;
# seed-config-match activates whenever a ticket is given.
set -euo pipefail

usage() { echo "usage: $0 --dir <run-dir> [--ticket <ticket-file>]" >&2; exit 64; }

DIR=""
TICKET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --ticket) TICKET="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$DIR" ] || usage
[ -d "$DIR" ] || { echo "FAIL setup: no such run dir: $DIR" >&2; exit 1; }

pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; exit 1; }

latest_checkpoint() {
  local d="$1" best="" best_n=-1 c n
  for c in "$d"/checkpoints/checkpoint_*/; do
    [ -d "$c" ] || continue
    n="${c%/}"; n="${n##*checkpoint_}"
    case "$n" in ''|*[!0-9]*) continue ;; esac
    if [ "$n" -gt "$best_n" ]; then best_n="$n"; best="${c%/}"; fi
  done
  printf '%s' "$best"
}

# --- 1. Ledger exact -------------------------------------------------------
check_ledger_exact() {
  local calls="$DIR/llm_calls.jsonl" ckpt null_rows call_sum ledger_spent
  [ -f "$calls" ] || fail "ledger-exact" "missing llm_calls.jsonl"
  ckpt=$(latest_checkpoint "$DIR")
  [ -n "$ckpt" ] || fail "ledger-exact" "no checkpoints/checkpoint_* dir found"
  [ -f "$ckpt/noema_state.json" ] || fail "ledger-exact" "missing $ckpt/noema_state.json"
  null_rows=$(jq -s '[.[] | select(.prompt_tokens == null or .completion_tokens == null)] | length' "$calls")
  call_sum=$(jq -s '[.[] | (.prompt_tokens // 0) + (.completion_tokens // 0)] | add // 0' "$calls")
  ledger_spent=$(jq '[.ledger.spent_by_account[]?] | add // 0' "$ckpt/noema_state.json")
  [ "$null_rows" -eq 0 ] || fail "ledger-exact" "$null_rows row(s) with null prompt_tokens/completion_tokens in llm_calls.jsonl"
  [ "$call_sum" -eq "$ledger_spent" ] || fail "ledger-exact" "sum(llm_calls.jsonl)=$call_sum != ledger.spent()=$ledger_spent ($ckpt)"
  pass "ledger-exact"
}

# --- 2. Budget respected -----------------------------------------------------
check_budget_respected() {
  local calls="$DIR/llm_calls.jsonl" ckpt budget spent max_call lower upper
  ckpt=$(latest_checkpoint "$DIR")
  [ -n "$ckpt" ] || fail "budget-respected" "no checkpoints/checkpoint_* dir found"
  budget=$(jq '.ledger.total_budget_tokens' "$ckpt/noema_state.json")
  spent=$(jq '[.ledger.spent_by_account[]?] | add // 0' "$ckpt/noema_state.json")
  max_call=$(jq -s '[.[] | (.prompt_tokens // 0) + (.completion_tokens // 0)] | if length==0 then 0 else max end' "$calls")
  upper=$((budget + max_call))
  lower=$((budget - max_call))
  [ "$spent" -le "$upper" ] || fail "budget-respected" "spent=$spent exceeds budget=$budget + one-call overshoot ($max_call)"
  [ "$spent" -ge "$lower" ] || fail "budget-respected" "spent=$spent is below budget=$budget - one-call margin ($max_call); run did not end via BudgetExhausted"
  pass "budget-respected"
}

# --- 3. Island distribution --------------------------------------------------
check_island_distribution() {
  local ckpt num_islands empty_islands
  ckpt=$(latest_checkpoint "$DIR")
  [ -n "$ckpt" ] || fail "island-distribution" "no checkpoints/checkpoint_* dir found"
  [ -f "$ckpt/metadata.json" ] || fail "island-distribution" "missing $ckpt/metadata.json"
  num_islands=$(jq '.islands | length' "$ckpt/metadata.json")
  empty_islands=$(jq '[.islands[] | select(length == 0)] | length' "$ckpt/metadata.json")
  [ "$num_islands" -ne 0 ] || fail "island-distribution" "no islands recorded in $ckpt/metadata.json"
  [ "$empty_islands" -eq 0 ] || fail "island-distribution" "$empty_islands of $num_islands islands hold zero stored programs ($ckpt/metadata.json)"
  pass "island-distribution"
}

# --- 4. Zero context-overflow -------------------------------------------------
check_zero_context_overflow() {
  local log="$DIR/run.log" count=0
  if [ -f "$log" ]; then
    count=$(grep -ciE 'context.?overflow' "$log" || true)
  fi
  [ "$count" -eq 0 ] || fail "zero-context-overflow" "$count context-overflow occurrence(s) in $log"
  pass "zero-context-overflow"
}

# --- ticket parsing (frontmatter between the first two "---" lines) --------
ticket_frontmatter() {
  awk '/^---[ \t]*$/{c++; next} c==1' "$TICKET"
}

ticket_scalar() {
  ticket_frontmatter | sed -nE "s/^$1:[ \t]*//p" | head -1
}

ticket_run_lines() {
  ticket_frontmatter | grep -E '^[ \t]*- \{'
}

flow_get() {
  printf '%s' "$1" | grep -oP "(?<=$2: )[^,}]+" | sed -E 's/^[ \t]+|[ \t]+$//g'
}

# resolve a run-entry's dir field and the matching manifest, if any
ticket_entry_for_dir() {
  local target line d
  target=$(realpath -m "$1")
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    d=$(flow_get "$line" dir)
    if [ "$(realpath -m "$d")" = "$target" ]; then printf '%s' "$line"; return 0; fi
  done < <(ticket_run_lines)
  return 1
}

# --- 5. Prompt identity (paired arms) ---------------------------------------
check_prompt_identity() {
  local other_dir="$1" sys_a sys_b prefix_a prefix_b
  sys_a="$DIR/run_manifest.json"; sys_b="$other_dir/run_manifest.json"
  [ -f "$sys_a" ] || fail "prompt-identity" "missing run_manifest.json in $DIR"
  [ -f "$sys_b" ] || fail "prompt-identity" "missing run_manifest.json in $other_dir"
  prefix_a=$(jq -r '.prompt_system | split("<<<COORD>>>")[0]' "$sys_a")
  prefix_b=$(jq -r '.prompt_system | split("<<<COORD>>>")[0]' "$sys_b")
  [ "$prefix_a" = "$prefix_b" ] || fail "prompt-identity" "shared prompt prefix differs between $DIR and $other_dir"
  pass "prompt-identity"
}

# --- 6. Config delta (paired arms) ------------------------------------------
check_config_delta() {
  local other_dir="$1" delta
  delta=$(jq -nc --slurpfile a "$DIR/run_manifest.json" --slurpfile b "$other_dir/run_manifest.json" '
    ($a[0].config) as $x | ($b[0].config) as $y
    | [ (($x|keys) + ($y|keys) | unique)[] as $k | select($x[$k] != $y[$k]) | $k ]')
  [ "$delta" = '["coordination_module"]' ] || fail "config-delta" "differing config keys=$delta, expected only [\"coordination_module\"]"
  pass "config-delta"
}

# --- 7. Seed/config sha match -------------------------------------------------
check_seed_config_match() {
  local entry ticket_sha ticket_seed manifest run_sha run_seed
  manifest="$DIR/run_manifest.json"
  [ -f "$manifest" ] || fail "seed-config-match" "missing run_manifest.json in $DIR"
  entry=$(ticket_entry_for_dir "$DIR") || fail "seed-config-match" "no ticket run entry resolves to $DIR"
  ticket_sha=$(ticket_scalar config_sha256)
  ticket_seed=$(flow_get "$entry" seed)
  run_sha=$(jq -r '.config_sha256' "$manifest")
  run_seed=$(jq -r '.seed' "$manifest")
  { [ "$run_sha" = "$ticket_sha" ] && [ "$run_seed" = "$ticket_seed" ]; } \
    || fail "seed-config-match" "run(sha=$run_sha,seed=$run_seed) != ticket(sha=$ticket_sha,seed=$ticket_seed)"
  pass "seed-config-match"
}

check_ledger_exact
check_budget_respected
check_island_distribution
check_zero_context_overflow

if [ -n "$TICKET" ]; then
  [ -f "$TICKET" ] || { echo "FAIL setup: no such ticket file: $TICKET" >&2; exit 1; }
  run_count=$(ticket_run_lines | grep -c '^' || true)
  if [ "$run_count" -eq 2 ]; then
    self_entry=$(ticket_entry_for_dir "$DIR" || true)
    other_dir=""
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      if [ "$line" = "$self_entry" ]; then continue; fi
      other_dir=$(flow_get "$line" dir)
    done < <(ticket_run_lines)
    if [ -n "$other_dir" ]; then
      check_prompt_identity "$other_dir"
      check_config_delta "$other_dir"
    fi
  fi
  check_seed_config_match
fi

exit 0
