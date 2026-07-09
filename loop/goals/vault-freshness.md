predicate: d=$(grep -oP '(?<=> Updated: )[0-9-]{10}' /root/claude-brain/INDEX.md | head -1) && [ $(( ($(date +%s) - $(date -d "$d" +%s)) / 86400 )) -le 7 ]
born: 2026-07-08
source: loop-system setup — the vault is the brain; a stale INDEX means the conductor routes on fiction (found 4 days stale at enrollment, missing the PES arm entirely)
status: satisfied
last-pass: 2026-07-08
on-violation: run the sync-vault-from-repo skill through the pipeline.
retire-when: the vault-loop system is retired. Human decision, logged.
