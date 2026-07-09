---
name: fix-test-debt
description: Fix a failing or flaky test in the noema suite by fixing the CODE it exercises.
when: triage reports a red test run, or the tests-green standing goal fails.
---

## Steps
1. Reproduce: `python3 -m unittest <failing module>` — capture the exact failure.
2. Locate the defect in `noema/` (not in the test).
3. Fix with the smallest possible diff; re-run the failing module, then the full suite.

## Never
- Never edit, weaken, skip, or delete a test to make it pass.
- Never touch `noema/coordination/base.py` — that queues for the user.
- Never exceed 200 changed lines.

## Done when
- The previously failing test passes; `python3 -m unittest discover tests` exits 0.
- The diff touches only `noema/` source (plus a new test if the fix warrants one).
