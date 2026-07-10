predicate: cd /root/noema-evolve && python3 -m unittest tests.test_noema_island_context 2>/dev/null && git diff --quiet HEAD -- noema/coordination/base.py && grep -q "island_bests_provider" noema/controller.py && grep -q "island_bests_provider" noema/coordination/pes/planner.py && ! grep -q "island_bests_provider" <(python3 -c "from noema.config import NoemaConfig; print(NoemaConfig().to_yaml())")
born: 2026-07-10
source: vault task 0061 (WP2 of the pes-faithful plan) — SubstrateDatabase.per_island_bests + controller-side provider injection (local params copy only, hash-safe) + Planner._island_status_block. Fresh-context verifier PASS 2026-07-10 with one deferred obligation carried into task 0063's done-when.
status: satisfied
last-pass: 2026-07-10
on-violation: a callable leaking into the frozen config breaks run-config hashing (verify-run invariant); queue, don't auto-fix.
retire-when: absorbed into 0063's faithful-prompt tests once the block is on the live path. Human decision, logged.
