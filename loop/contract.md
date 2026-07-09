# Contract — blast radius, declared before the first tick

## acts alone
- draft PRs on `loop/*` branches
- fix test debt (failing/flaky tests, without weakening them)
- update vault INDEX.md, vault knowledge notes, `.vault-loop/log.md`
- update `loop/memory/` (STATE, ledgers, dispatch log)
- read-only analysis of existing run logs (`llm_calls.jsonl`, checkpoints)
- label and triage GitHub issues
- metering defect fixes that restore a VIOLATED standing goal, when the fix
  extends `tests/test_noema_budget_*.py` in the same diff and stays <= 200 lines
  (carve-out: `noema/budget/` is otherwise queued — see below)
- behavior-preserving refactors and non-triad bug fixes in `noema/`, excluding
  `noema/coordination/base.py`, `noema/budget/` (outside the carve-out above),
  and prompt-identity modules; suite must stay green; diff <= 200 lines

## queues for me
- any change to `noema/coordination/base.py` (the interface)
- any change to `noema/budget/` semantics EXCEPT the goal-restoring metering
  defect carve-out above (ledger/metering/accounts fixes that the loop may attempt)
- experiment configs that change the comparison basis (budget, seeds, arms, templates)
- any live LLM run (real provider or local inference node)
- any new dependency
- any diff > 400 lines
- any skill below "auto" tier in `loop/memory/trust.tsv`

## wakes me up  (channel: /home/archie/scripts/send-telegram.sh)
- verify fails twice on the same item
- safeguard router swapped models mid-run
- daily usage cap breached mid-tick (notional throttle, not money — see RUNBOOK)
- anything requests a secret
- a standing goal in `loop/goals/` is VIOLATED
- a live run crashes mid-study
