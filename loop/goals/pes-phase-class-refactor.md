predicate: cd /root/noema-evolve && python3 -m unittest tests.test_noema_pes tests.test_noema_pes_phases 2>/dev/null && test -f noema/coordination/pes/planner.py && test -f noema/coordination/pes/executor.py && test -f noema/coordination/pes/summarizer.py && [ "$(grep -c 'generate_with_context\|PLANNER_SYSTEM = \|REFLECTION_SYSTEM = ' noema/coordination/pes/module.py)" -eq 0 ] && grep -q '"parent_id": ctx.parent.id' noema/coordination/pes/summarizer.py
born: 2026-07-10
source: vault task 0060 (WP1 of the pes-faithful plan) — PES module split into planner/executor/summarizer phase classes behind the unchanged PESPlannerModule façade, behavior-identical, _plans entries carry parent_id. Fresh-context verifier PASS 2026-07-10; verify.sh green.
status: satisfied
last-pass: 2026-07-10
on-violation: the pes-faithful prompt ports (tasks 0063-0065) build on this layout — a regression here breaks their landing spot. Queue, don't auto-fix.
retire-when: absorbed into the arm-registry capability tests after task 0066 lands. Human decision, logged.
