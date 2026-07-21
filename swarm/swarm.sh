#!/usr/bin/env bash
# Minimal agent swarm: GitHub issues -> automated PRs, reviewed, human-merged.
#
# Design: GitHub owns task state (issues + labels). Git owns code state
# (worktrees + branches). Claude Code is the worker. The SCRIPT does all
# git/gh/push work; the agent only edits files inside an isolated worktree,
# so an unattended run cannot touch the remote, secrets, or the merge gate.
#
# Usage:
#   ./swarm/swarm.sh setup                 # create the agent:* labels (once)
#   ./swarm/swarm.sh run <issue-number>    # process one agent:ready issue
#   ./swarm/swarm.sh watch [seconds]       # cron-poll agent:ready issues (default 300s)
set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
PROMPTS="$REPO_ROOT/swarm/prompts"
WORKSPACES="${SWARM_WORKSPACES:-$REPO_ROOT/../noema-swarm-workspaces}"
# Model tiers: strong model for high-level judgment (coordinator, doc, review),
# cheap model for clearly-defined coding (implementer).
STRONG="${SWARM_MODEL_STRONG:-claude-fable-5}"
CODE="${SWARM_MODEL_CODE:-claude-sonnet-5}"
BUDGET="${SWARM_BUDGET_USD:-3}"
BASE="${SWARM_BASE:-main}"
NWO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

log() { printf '[swarm %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

# --- label state machine ------------------------------------------------------
setup_labels() {
  local pairs=(
    "agent:ready|2da44e|Human-approved: safe to start the agent"
    "agent:implementing|fbca04|Worker is editing code"
    "agent:reviewing|1d76db|PR open, awaiting agent + human review"
    "agent:blocked|d73a4a|Worker produced no change / failed"
    "agent:needs-human|b60205|Escalated: ambiguous or risky"
    "agent:done|6f42c1|Merged"
    "type:documentation|0075ca|Documentation task (uses DOC-STANDARD.md)"
  )
  for p in "${pairs[@]}"; do
    IFS='|' read -r name color desc <<<"$p"
    gh label create "$name" --color "$color" --description "$desc" --force
  done
  log "labels ready"
}

relabel() { # issue, remove, add
  gh issue edit "$1" --remove-label "$2" --add-label "$3" 2>/dev/null || \
    gh issue edit "$1" --add-label "$3"
}

# --- one issue ----------------------------------------------------------------
run_one() {
  local n="$1"
  local labels title body slug branch wt prompt_file review base_sha
  labels="$(gh issue view "$n" --json labels -q '[.labels[].name]|join(",")')"
  if [[ ",$labels," != *",agent:ready,"* ]]; then
    log "issue #$n not agent:ready (labels: $labels) - skipping"; return 0
  fi
  title="$(gh issue view "$n" --json title -q .title)"
  body="$(gh issue view "$n" --json body -q .body)"
  slug="$(echo "$title" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | sed 's/-\+/-/g;s/^-//;s/-$//' | cut -c1-40)"
  branch="agent/issue-${n}-${slug}"
  wt="$WORKSPACES/issue-$n"

  log "issue #$n: $title"

  # coordinator (strong model): plan the work, or escalate if under-specified
  local plan
  plan="$(claude -p "$(cat "$PROMPTS/coordinator.md")

## Issue #$n: $title

$body" --model "$STRONG" --max-budget-usd "$BUDGET" \
      --add-dir "$REPO_ROOT" --disallowedTools "Edit,Write,Bash" 2>/dev/null || echo "PROCEED")"
  if [[ "$plan" == ESCALATE* ]]; then
    log "issue #$n: coordinator escalated -> needs-human"
    relabel "$n" agent:ready agent:needs-human
    gh issue comment "$n" --body "## Coordinator: needs human"$'\n\n'"$plan"
    return 0
  fi
  gh issue comment "$n" --body "## Coordinator plan"$'\n\n'"$plan"
  relabel "$n" agent:ready agent:implementing

  # fresh worktree off the latest base branch
  git -C "$REPO_ROOT" fetch -q origin "$BASE"
  rm -rf "$wt"; mkdir -p "$WORKSPACES"
  git -C "$REPO_ROOT" worktree remove --force "$wt" 2>/dev/null || true
  git -C "$REPO_ROOT" branch -D "$branch" 2>/dev/null || true
  git -C "$REPO_ROOT" worktree add -q -b "$branch" "$wt" "origin/$BASE"
  base_sha="$(git -C "$wt" rev-parse HEAD)"

  # pick role + model: doc tasks use the strong model for language precision;
  # clearly-defined coding goes to the cheap model.
  local worker_model
  if [[ ",$labels," == *",type:documentation,"* ]]; then
    prompt_file="$PROMPTS/doc.md"; worker_model="$STRONG"
  else
    prompt_file="$PROMPTS/implementer.md"; worker_model="$CODE"
  fi

  # run the worker inside the worktree (edits files only), given the plan
  ( cd "$wt" && claude -p "$(cat "$prompt_file")

## Coordinator plan
$plan

## Issue #$n: $title

$body" \
      --model "$worker_model" --max-budget-usd "$BUDGET" --dangerously-skip-permissions \
      --add-dir "$REPO_ROOT" >/dev/null ) || log "worker exited non-zero (continuing)"

  if git -C "$wt" diff --quiet && git -C "$wt" diff --cached --quiet; then
    log "issue #$n: no changes produced -> blocked"
    relabel "$n" agent:implementing agent:blocked
    gh issue comment "$n" --body "Swarm worker produced no file changes. Marking \`agent:blocked\`. The task may be under-specified or already done."
    git -C "$REPO_ROOT" worktree remove --force "$wt" || true
    return 0
  fi

  git -C "$wt" add -A
  git -C "$wt" commit -q -m "$(printf '%s\n\nCloses #%s' "$title" "$n")"
  git -C "$wt" push -q -u origin "$branch"

  gh pr create --head "$branch" --base "$BASE" \
    --title "$title" \
    --body "$(printf '## Summary\n\nAutomated change for issue #%s.\n\n## Validation\n\nSee CI checks and the agent review below.\n\nCloses #%s' "$n" "$n")"
  relabel "$n" agent:implementing agent:reviewing

  # reviewer: reads the diff, never approves, posts structured comment
  review="$(cd "$wt" && git diff "$base_sha"..HEAD | claude -p "$(cat "$PROMPTS/reviewer.md")

## Issue #$n: $title

$body

## Diff to review (follows)
$(cat)" \
      --model "$STRONG" --max-budget-usd "$BUDGET" \
      --disallowedTools "Edit,Write,Bash" 2>/dev/null || echo "_Reviewer failed to run._")"
  gh pr comment "$branch" --body "$review" || \
    gh pr comment "$(gh pr list --head "$branch" --json number -q '.[0].number')" --body "$review"

  git -C "$REPO_ROOT" worktree remove --force "$wt" || true
  log "issue #$n: PR opened, review posted. Human merge required."
}

# --- cron poll ----------------------------------------------------------------
watch() {
  local interval="${1:-300}"
  log "watching for agent:ready issues every ${interval}s (Ctrl-C to stop)"
  while true; do
    for n in $(gh issue list --label agent:ready --state open --json number -q '.[].number'); do
      run_one "$n" || log "issue #$n failed"
    done
    sleep "$interval"
  done
}

case "${1:-}" in
  setup) setup_labels ;;
  run)   run_one "${2:?usage: run <issue-number>}" ;;
  watch) watch "${2:-300}" ;;
  *) echo "usage: $0 {setup|run <n>|watch [seconds]}" >&2; exit 2 ;;
esac
