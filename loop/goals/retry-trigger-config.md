predicate: cd /root/noema-evolve && python3 -m unittest tests.test_noema_controller tests.test_noema_budget_ledger 2>/dev/null && grep -q 'retry_on: str = "failure"' noema/config.py && grep -q 'retry_on == "non_improvement"' noema/controller.py
born: 2026-07-10
source: vault task 0062 (WP3 of the pes-faithful plan, Decision #28) — retry_on config knob; non_improvement branch with keep-best semantics; per-attempt metering + BudgetExhausted mid-retry pinned in the budget-ledger tests (triad law: same commit as the controller change, 639f64f). Fresh-context verifier PASS 2026-07-10; its PLAUSIBLE finding (trailing-failure state leak) pinned false by an added test.
status: satisfied
last-pass: 2026-07-10
on-violation: metering integrity is guarantee triad #2 — a retry attempt spending unmetered tokens invalidates equal-token-spend comparison. Wake me.
retire-when: absorbed into the standing metering-integrity goal after the pes-faithful shakedown (RT-0003) validates retry accounting live. Human decision, logged.
