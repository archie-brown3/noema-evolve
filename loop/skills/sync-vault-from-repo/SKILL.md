---
name: sync-vault-from-repo
description: Reconcile the claude-brain vault (INDEX.md, task notes, knowledge notes) with repo reality — commits, branches, run dirs the vault doesn't know about.
when: triage reports vault INDEX stale (Updated > 7 days, or commits newer than the newest vault log entry), or the vault-freshness goal fails.
---

## Steps
1. Diff reality vs vault: `git log --oneline -20` and `ls -dt examples/*/noema_*output*`
   against `INDEX.md` and `.vault-loop/log.md` in `/root/claude-brain`.
2. Update task note statuses that reality has overtaken (done work → `Output / notes`
   filled, status flipped; superseded work → annotated, never deleted).
3. Refresh `INDEX.md`: Now/Next/Blocked/Recently-done sections, header timestamp
   (UTC ISO-8601), active-task count.
4. Append one line per change to `.vault-loop/log.md`.

## Never
- Never delete a vault file or a task note (vault never-delete rule).
- Never mark a task done unless its "Done when" checklist verifiably passed.
- Never invent decisions the user hasn't made — stale awaiting-user items stay Blocked.

## Done when
- `INDEX.md` "> Updated:" timestamp is today and every entry links to a real file.
- No repo commit newer than the newest `.vault-loop/log.md` entry is unexplained.
