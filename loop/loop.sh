#!/usr/bin/env bash
# The heartbeat. Triage (haiku) -> conduct (fable) -> execute (sonnet, worktree)
# -> verify (fresh fable) -> gate (verify.sh) -> trust ledger -> usage check.
# Exit map: 0 quiet/done, 1 iteration cap or seat failure, 2 reroute,
#           3 usage cap, 4 provider session limit (resets on its own).
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(git rev-parse --show-toplevel)"

MAX_ITERS="${MAX_ITERS:-10}"
# Notional units (the CLI's total_cost_usd). Subscription plan: nothing is
# billed — this caps runaway loops before they eat the session limit.
DAILY_USAGE_CAP="${DAILY_USAGE_CAP:-15}"
VAULT="${VAULT:-/root/claude-brain}"
TRIAGE_MODEL="${TRIAGE_MODEL:-haiku}"
WORKER_MODEL="${WORKER_MODEL:-sonnet}"
BRAIN_MODEL="${BRAIN_MODEL:-claude-fable-5}"
FALLBACK_MODEL="${FALLBACK_MODEL:-claude-opus-4-8}"
SEAT_TIMEOUT="${SEAT_TIMEOUT:-1800}"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-64000}"

mkdir -p memory/state-archive
touch memory/STATE.md memory/trust.tsv memory/dispatch.tsv memory/usage.log

wake_user() {
  echo "WAKE: $1" >&2
  [ -x /home/archie/scripts/send-telegram.sh ] \
    && /home/archie/scripts/send-telegram.sh "noema loop: $1" >/dev/null 2>&1 || true
}

# Rotate STATE.md: keep the newest 200 lines, archive the rest.
rotate_state() {
  local n; n=$(wc -l < memory/STATE.md)
  if [ "$n" -gt 200 ]; then
    head -n $((n - 200)) memory/STATE.md >> "memory/state-archive/$(date +%F).md"
    tail -n 200 memory/STATE.md > memory/STATE.md.t && mv memory/STATE.md.t memory/STATE.md
  fi
}

# mark_vault <status> — flip the dispatched vault task's frontmatter status,
# so the next tick's conductor skips an in-flight item instead of re-executing
# it. No-op for non-vault work orders (gh issues, goals, conductor-invented specs).
# Keys off .item from work-order.json; robust to path / slug / id-only formats.
mark_vault() {
  local item f
  item=$(jq -r .item work-order.json 2>/dev/null)
  [ -z "$item" ] || [ "$item" = null ] && return 0
  f="$VAULT/$item.md";          [ -f "$f" ] && { sed -i "s/^status:.*/status: $1/" "$f"; return 0; }
  f="$VAULT/tasks/$item.md";    [ -f "$f" ] && { sed -i "s/^status:.*/status: $1/" "$f"; return 0; }
  case "$item" in
    [0-9][0-9][0-9][0-9]*) for f in "$VAULT/tasks/${item}"*.md; do [ -f "$f" ] && { sed -i "s/^status:.*/status: $1/" "$f"; return 0; }; done ;;
  esac
  return 0
}

# check_response <stage> <json-file> [required-model-substring ...]
# Logs real cost, catches refusals (HTTP 200, stop_reason=refusal) and errors.
# Returns 2 if the serving model matches none of the allowed substrings.
check_response() {
  local stage="$1" f="$2"; shift 2
  ./scripts/log-cost.sh "$stage" "$(jq -r '.total_cost_usd // 0' "$f")"
  if [ "$(jq -r '.is_error // false' "$f")" = "true" ]; then
    local msg; msg=$(jq -r '.result // .subtype' "$f" | head -c 200)
    if grep -qiE 'session limit|usage limit|rate limit' <<<"$msg"; then
      echo "- SESSION-LIMIT($stage): $msg" >> memory/STATE.md
      return 4
    fi
    echo "- ERROR($stage): $msg" >> memory/STATE.md
    return 1
  fi
  if [ "$(jq -r '.stop_reason // ""' "$f")" = "refusal" ]; then
    echo "- REFUSAL($stage): see RUNBOOK — audit the prompt, rerun via fallback" >> memory/STATE.md
    return 1
  fi
  if [ "$#" -gt 0 ]; then
    local served ok=0 m
    served=$(jq -r '.modelUsage | keys[0] // ""' "$f")
    for m in "$@"; do [[ "$served" == *"$m"* ]] && ok=1; done
    if [ "$ok" -eq 0 ]; then
      echo "- rerouted($stage): served=$served" >> memory/STATE.md
      return 2
    fi
  fi
  return 0
}

# Pre-tick cap breach is routine throttling (already known) — log, don't page.
./scripts/cost-check.sh --cap "$DAILY_USAGE_CAP" || exit 3
rotate_state

for ((i=1; i<=MAX_ITERS; i++)); do
  # ---- Session archive: save all LLM outputs for post-tick review ----
  SESSION_DIR="memory/sessions/iter$i"
  mkdir -p "$SESSION_DIR"

  # ---- 1 TRIAGE: cheap reader on assembled signals; fresh file, then append ----
  SIG=$(mktemp); TRI=$(mktemp)
  {
    echo "== git log =="; git -C "$REPO_ROOT" log --oneline -20
    echo "== gh issues =="; gh issue list --limit 20 2>/dev/null || true
    echo "== gh runs =="; gh run list --limit 10 2>/dev/null || true
    echo "== vault INDEX (Now/Blocked) =="
    awk '/^## Now/,/^## Next/' "$VAULT/INDEX.md" 2>/dev/null || true
    awk '/^## Blocked/,/^## Recently/' "$VAULT/INDEX.md" 2>/dev/null || true
    echo "== goal ledger tail =="; tail -6 memory/goal-ledger.tsv 2>/dev/null || true
    echo "== newest run dirs =="
    ls -dt "$REPO_ROOT"/examples/*/noema_*output* 2>/dev/null | head -5 || true
  } > "$SIG"
  timeout "$SEAT_TIMEOUT" claude -p "$(cat triage.md)" --model "$TRIAGE_MODEL" \
    --allowedTools "" --output-format json < "$SIG" > /tmp/triage.json || true
  cp /tmp/triage.json "$SESSION_DIR/triage.json" 2>/dev/null || true
  cp "$SIG" "$SESSION_DIR/signals.txt" 2>/dev/null || true
  RC=0; check_response triage /tmp/triage.json || RC=$?
  [ "$RC" -eq 4 ] && exit 4
  [ "$RC" -ne 0 ] && exit 1
  jq -r '.result' /tmp/triage.json > "$TRI"
  { echo ""; echo "## tick $(date -Is) iter $i"; cat "$TRI"; } >> memory/STATE.md
  grep -q "status: actionable" "$TRI" || { echo "quiet"; exit 0; }

  # ---- 2 CONDUCT: fable, effort high, read-only, JSON decision ----
  timeout "$SEAT_TIMEOUT" claude -p "$(cat conductor.md)

STATE:
$(tail -80 memory/STATE.md)

TRUST LEDGER:
$(./scripts/trust-log.sh --render)

CONTRACT:
$(cat contract.md)

VAULT INDEX:
$(cat "$VAULT/INDEX.md" 2>/dev/null || echo '(vault unavailable)')" \
    --model "$BRAIN_MODEL" --fallback-model "$FALLBACK_MODEL" --effort high \
    --allowedTools "Read" --add-dir /root/.claude/skills/vault-loop --output-format json > /tmp/c.json || true
  cp /tmp/c.json "$SESSION_DIR/conductor.json" 2>/dev/null || true
  RC=0; check_response conductor /tmp/c.json fable opus-4-8 || RC=$?
  [ "$RC" -eq 2 ] && { wake_user "safeguard router swapped models mid-run"; exit 2; }
  [ "$RC" -eq 4 ] && exit 4
  [ "$RC" -ne 0 ] && exit 1

  # Strip accidental code fences, validate the five fields.
  jq -r '.result' /tmp/c.json | sed '/^```/d' > work-order.json
  cp work-order.json "$SESSION_DIR/work-order.json" 2>/dev/null || true
  for k in action item skill spec done_when; do
    jq -e ".$k" work-order.json >/dev/null \
      || { echo "- malformed work order (missing $k)" >> memory/STATE.md; exit 1; }
  done
  SKILL=$(jq -r .skill work-order.json); ACTION=$(jq -r .action work-order.json)
  echo -e "$(date -Is)\titer$i\t$ACTION\t$SKILL\t$(jq -r .item work-order.json | head -c 120)" >> memory/dispatch.tsv
  [ "$ACTION" = stop  ] && exit 0
  [ "$ACTION" = queue ] && { echo "- queued: $SKILL — $(jq -r .item work-order.json)" >> memory/STATE.md; continue; }

  # ---- 3 EXECUTE: sonnet with real tools in an isolated worktree ----
  mark_vault in-progress
  WT="$REPO_ROOT/../wt-$i"
  git -C "$REPO_ROOT" worktree add "$WT" -b "loop/$SKILL-$(date +%s)" >/dev/null
  ( cd "$WT" && timeout "$SEAT_TIMEOUT" claude -p "$(cat "$OLDPWD/workers/implement.md")

WORK ORDER:
$(cat "$OLDPWD/work-order.json")" \
      --model "$WORKER_MODEL" --effort medium \
      --allowedTools "Read Glob Grep Edit Write Bash(python3 -m unittest:*) Bash(git diff:*) Bash(git status:*) Bash(git log:*) Bash(git checkout:*) Bash(git clean:*)" \
      --output-format json > /tmp/w.json || true )
  cp /tmp/w.json "$SESSION_DIR/worker.json" 2>/dev/null || true
  RC=0; check_response worker /tmp/w.json || RC=$?
  if [ "$RC" -ne 0 ]; then
    mark_vault todo; git -C "$REPO_ROOT" worktree remove --force "$WT"
    [ "$RC" -eq 4 ] && exit 4; exit 1
  fi

  # ---- 4 VERIFY: fresh fable, no tools, sees only spec + diff ----
  timeout "$SEAT_TIMEOUT" claude -p "$(cat workers/verify.md)

SPEC:
$(jq -r .spec work-order.json)
DONE_WHEN:
$(jq -r '.done_when[]' work-order.json)

DIFF:
$(git -C "$WT" diff; git -C "$WT" status --short)" \
    --model "$BRAIN_MODEL" --fallback-model "$FALLBACK_MODEL" --effort high \
    --allowedTools "" --output-format json > /tmp/v.json || true
  cp /tmp/v.json "$SESSION_DIR/verifier.json" 2>/dev/null || true
  RC=0; check_response verifier /tmp/v.json || RC=$?
  if [ "$RC" -ne 0 ]; then
    mark_vault todo
    [ "$RC" -eq 4 ] && exit 4; exit 1
  fi
  V=$(jq -r '.result' /tmp/v.json | head -1)
  STAT=$(git -C "$WT" diff --shortstat | xargs)

  # ---- 5 GATE: deterministic final vote; then the trust ledger ----
  if [[ "$V" == PASS* ]] && ( cd "$WT" && ./loop/guardrails/verify.sh >/dev/null 2>&1 ); then
    ./scripts/trust-log.sh "$SKILL" pass
    if [ "$(./scripts/trust-log.sh --tier "$SKILL")" = auto ]; then
      ( cd "$WT" && git add -A && git commit -qm "loop: $SKILL

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
        && gh pr create --fill --draft 2>/dev/null || true )
      echo "- shipped: $SKILL ($V) [$STAT]" >> memory/STATE.md
    else
      ( cd "$WT" && git add -A && git commit -qm "loop draft: $SKILL

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" ) || true
      echo "- review: $SKILL in $WT ($V) [$STAT]" >> memory/STATE.md
    fi
  else
    mark_vault todo
    ./scripts/trust-log.sh "$SKILL" fail
    echo "- FAILED: $SKILL in $WT — $V" >> memory/STATE.md
    [ "$(grep -c "FAILED: $SKILL" memory/STATE.md)" -ge 2 ] \
      && wake_user "verify failed twice on $SKILL — human needed"
  fi
  ./scripts/cost-check.sh --cap "$DAILY_USAGE_CAP" || { wake_user "daily usage cap reached (notional throttle — nothing billed)"; exit 3; }
done
echo "- iteration cap reached without stop" >> memory/STATE.md
exit 1
