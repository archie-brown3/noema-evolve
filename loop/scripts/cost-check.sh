#!/usr/bin/env bash
# usage: cost-check.sh --cap <units>   exit 1 when today's notional usage >= cap
#        cost-check.sh --report        last 7 days by stage
# Units are the CLI's total_cost_usd figures. On a subscription plan nothing is
# billed — this is a relative usage meter / runaway throttle, not money.
set -euo pipefail
F="$(dirname "$0")/../memory/usage.log"; touch "$F"; TODAY=$(date +%F)
case "${1:-}" in
  --cap)
    spent=$(awk -F'\t' -v d="$TODAY" '$1 ~ d {s+=$3} END{printf "%.4f",s}' "$F")
    awk -v s="$spent" -v b="$2" 'BEGIN{exit (s>=b)?1:0}' \
      || { echo "notional usage $spent of cap $2 today (throttle — nothing billed)" >&2; exit 1; };;
  --report)
    awk -F'\t' -v since="$(date -d '7 days ago' +%F)" \
      '$1>=since{s[$2]+=$3;t+=$3} END{for(k in s) printf "  %-10s $%.4f\n",k,s[k]; printf "  TOTAL      $%.4f\n",t}' "$F";;
  *) echo "usage: $0 --cap <units> | --report" >&2; exit 64;;
esac
