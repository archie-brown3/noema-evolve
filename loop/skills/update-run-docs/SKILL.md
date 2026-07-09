---
name: update-run-docs
description: Keep README.md and examples/*/README docs truthful against the code (arms list, config keys, commands).
when: triage reports a commit that changed config keys, module names, or CLI flags without a doc update.
---

## Steps
1. Diff the claimed docs against reality: config keys in `noema/config.py`, arms in
   `noema/coordination/__init__.py`, commands in `examples/*/run_noema_arm.py`.
2. Fix only falsehoods and omissions — no rewrites, no style passes.

## Never
- Never document features that don't exist yet.
- Never change code to match docs — docs follow code.

## Done when
- Every command in the touched docs runs as written (or is marked as requiring a
  live node); every config key named exists in `noema/config.py`.
