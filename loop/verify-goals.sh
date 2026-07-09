#!/usr/bin/env bash
# Standing-goal sentinel: every finished thing keeps getting re-verified, forever.
# A goal you only verify once is an assumption with a timestamp.
# Detection only — fixes go through the normal pipeline (loop.sh).
set -uo pipefail
cd "$(dirname "$0")"
LEDGER="memory/goal-ledger.tsv"; VIOLATIONS=0
for g in goals/*.md; do
  [ -e "$g" ] || continue
  grep -q '^status: retired' "$g" && continue
  pred=$(grep '^predicate:' "$g" | cut -d' ' -f2-); name=$(basename "$g" .md)
  start=$(date +%s%3N)
  if timeout 60 bash -c "$pred" >/dev/null 2>&1; then r=pass
    sed -i "s/^status:.*/status: satisfied/; s/^last-pass:.*/last-pass: $(date +%F)/" "$g"
  else r=FAIL; VIOLATIONS=$((VIOLATIONS+1)); sed -i "s/^status:.*/status: VIOLATED/" "$g"; fi
  echo -e "$(date -Is)\t$name\t$r\t$(( $(date +%s%3N) - start ))" >> "$LEDGER"
done
if [ "$VIOLATIONS" -gt 0 ]; then
  V=$(grep -l '^status: VIOLATED' goals/*.md | xargs -n1 basename 2>/dev/null | tr '\n' ' ')
  echo "VIOLATED: $V"
  [ -x /home/archie/scripts/send-telegram.sh ] \
    && /home/archie/scripts/send-telegram.sh "noema goals VIOLATED: $V" >/dev/null 2>&1 || true
  exit 1
fi
echo "all standing goals hold"
