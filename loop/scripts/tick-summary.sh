#!/usr/bin/env bash
# tick-summary.sh — human-readable summary of today's loop ticks.
# Run after ticks complete (e.g. after 08:30 UTC).
# Usage: ./scripts/tick-summary.sh [date]
#   date defaults to today (UTC).
set -euo pipefail
cd "$(dirname "$0")/.."

DATE="${1:-$(date -u +%F)}"
STATE="memory/STATE.md"
DISPATCH="memory/dispatch.tsv"
CRONLOG="memory/cron.log"
USAGE="memory/usage.log"
SESSIONS="memory/sessions"

echo "=== Loop Tick Summary: $DATE ==="
echo ""

# --- Cron execution ---
echo "--- Cron Fires ---"
if [ -f "$CRONLOG" ]; then
  grep -E "^===|iteration|quiet|exit|SESSION-LIMIT|WAKE|cap" "$CRONLOG" 2>/dev/null || echo "(no cron activity)"
else
  echo "(no cron.log)"
fi
echo ""

# --- Dispatches ---
echo "--- Dispatches ---"
if [ -f "$DISPATCH" ]; then
  awk -F'\t' -v d="$DATE" '$1 ~ d {printf "  %s  iter%s  %-10s  %s  %s\n", $1, $2, $3, $4, $5}' "$DISPATCH" 2>/dev/null || echo "(none today)"
else
  echo "(no dispatch.tsv)"
fi
echo ""

# --- Cost ---
echo "--- Cost by Stage ---"
if [ -f "$USAGE" ]; then
  awk -F'\t' -v d="$DATE" '$1 ~ d {s[$2]+=$3; n[$2]++} END {for (k in s) printf "  %-12s $%.4f  (%d calls)\n", k, s[k], n[k]}' "$USAGE" 2>/dev/null || echo "(no usage data)"
else
  echo "(no usage.log)"
fi
echo ""

# --- Session files ---
echo "--- Session Archives ---"
if [ -d "$SESSIONS" ]; then
  for dir in "$SESSIONS"/iter*/; do
    [ -d "$dir" ] || continue
    iter=$(basename "$dir")
    echo "  [$iter]"
    # Triage finding
    if [ -f "$dir/triage.json" ]; then
      result=$(jq -r '.result // "(no result)"' "$dir/triage.json" 2>/dev/null | head -5)
      echo "    triage: $result"
    fi
    # Conductor decision
    if [ -f "$dir/conductor.json" ]; then
      result=$(jq -r '.result // "(no result)"' "$dir/conductor.json" 2>/dev/null | sed '/^```/d' | head -3)
      echo "    conduct: $result"
    fi
    # Work order
    if [ -f "$dir/work-order.json" ]; then
      action=$(jq -r '.action // "?"' "$dir/work-order.json" 2>/dev/null)
      item=$(jq -r '.item // "?"' "$dir/work-order.json" 2>/dev/null)
      skill=$(jq -r '.skill // "?"' "$dir/work-order.json" 2>/dev/null)
      echo "    work order: action=$action item=$item skill=$skill"
    fi
    # Worker result
    if [ -f "$dir/worker.json" ]; then
      result=$(jq -r '.result // "(no result)"' "$dir/worker.json" 2>/dev/null | head -3)
      echo "    worker: $result"
    fi
    # Verdict
    if [ -f "$dir/verifier.json" ]; then
      result=$(jq -r '.result // "(no result)"' "$dir/verifier.json" 2>/dev/null | head -1)
      echo "    verify: $result"
    fi
  done
else
  echo "(no sessions directory)"
fi
echo ""

# --- STATE.md tail ---
echo "--- STATE.md (last 40 lines) ---"
tail -40 "$STATE" 2>/dev/null || echo "(no STATE.md)"
