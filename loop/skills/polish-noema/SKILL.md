---
name: polish-noema
description: Proactively improve one non-triad noema/ source module when the curated vault queue is quiet.
when: conductor finds no unblocked vault task and no breakage/goal violation this tick; budget allows.
---

## Steps
1. Pick ONE concrete non-triad module under `noema/` (exact path named in the
   spec). Determine the single highest-value change for it this tick from:
   - docstring/docs gaps that obscure the module's contract
   - type annotations missing on public functions
   - a behavior-preserving refactor that simplifies a tangle (extract helper,
     name things, collapse duplication) — behavior MUST be unchanged
   - a real bug in a non-triad module (a path the tests already cover, or one
     you can pin with a new passing test)
2. Make the smallest diff that achieves it. Re-run the module's test file, then
   the full suite: `python3 -m unittest discover tests`.
3. If the change reveals a bug you cannot fix within this skill's scope (e.g. it
   lives in a triad module), STOP this skill, write a one-line finding to
   IMPLEMENTATION.md, and leave the module unchanged. Queue `fix-test-debt` or a
   new vault task for the bug; do not fix it inline here.

## Never
- Never touch `noema/coordination/base.py`, `noema/budget/` (metering is a
  carve-out handled by a different skill), or prompt-identity modules
  (`noema/prompts/` or equivalent).
- Never touch experiment run dirs, checkpoints, `llm_calls.jsonl`, dependency
  manifests, or anything under `examples/` other than read-only inspection.
- Never change observable behavior of public functions unless the spec named a
  specific bug and you are fixing it (with a new passing test pinning the fix).
- Never edit, weaken, skip, or delete an existing test to make it pass.
- Never exceed 200 changed lines. If the improvement needs more, ship the
  smallest safe slice and note the remainder in IMPLEMENTATION.md.

## Done when
- The full suite passes: `python3 -m unittest discover tests` exits 0.
- The diff is <= 200 lines and touches only non-triad `noema/` source (plus a
  new test file if the change warrants one).
- No file in the Never list above was modified.
- The change matches the exact intent stated in the conductor's spec; nothing
  extra was refactored opportunistically.
