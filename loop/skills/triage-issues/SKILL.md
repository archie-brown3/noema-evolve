---
name: triage-issues
description: Label, deduplicate, and summarize open GitHub issues on noema-evolve.
when: triage reports unlabeled or stale open issues.
---

## Steps
1. `gh issue list --limit 50` — find unlabeled/duplicate/stale issues.
2. Label by area (budget, coordination, substrate, experiments, loop) and size.
3. Close exact duplicates with a link to the survivor; comment one-line summaries.

## Never
- Never close a non-duplicate issue.
- Never create new issues for work the vault already tracks — link to the vault task id.

## Done when
- Every open issue has at least one area label.
- Zero exact duplicates remain open.
