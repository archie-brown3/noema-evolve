"""
Acceptance tests for the ONLINE bin-packing benchmark (tasks 0036 + 0091).

Verify the harness mechanics (subprocess timeout, determinism, valid solution)
AND that the redesign gave the benchmark real evolutionary headroom for C3: the
best-fit initial program scores below 1.0, and the score discriminates heuristic
quality (a worse heuristic scores strictly lower), so there is a gradient an
evolved heuristic can climb. This replaces the task-0036 tripwire that pinned the
degenerate offline score of 1.0.
"""

import importlib.util
import os
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_PACKING = os.path.join(REPO, "examples", "bin_packing")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN_PACKING, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _program_with_priority(tmpdir, priority_src):
    """Write a copy of initial_program.py with its priority body replaced."""
    with open(os.path.join(BIN_PACKING, "initial_program.py")) as f:
        src = f.read()
    head = src.split("# EVOLVE-BLOCK-START")[0]
    path = os.path.join(tmpdir, "candidate.py")
    with open(path, "w") as f:
        f.write(head)
        f.write("# EVOLVE-BLOCK-START\n")
        f.write(priority_src)
        f.write("# EVOLVE-BLOCK-END\n")
    return path


class TestBinPackingHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = _load("bp_eval", "evaluator.py")
        cls.initial = os.path.join(BIN_PACKING, "initial_program.py")

    def test_initial_program_evaluates_to_a_valid_solution(self):
        r = self.ev.evaluate(self.initial)
        self.assertEqual(r["validity"], 1.0)
        self.assertGreater(r["combined_score"], 0.0)
        self.assertLessEqual(r["combined_score"], 1.0)
        self.assertGreaterEqual(r["bins_used"], r["lower_bound"])
        self.assertEqual(r["num_instances"], 5)

    def test_scoring_is_deterministic(self):
        a = self.ev.evaluate(self.initial)
        b = self.ev.evaluate(self.initial)
        self.assertEqual(a["combined_score"], b["combined_score"])
        self.assertEqual(a["bins_used"], b["bins_used"])

    def test_hostile_program_times_out_without_killing_the_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            hostile = os.path.join(tmp, "hostile.py")
            with open(hostile, "w") as f:
                f.write("def run_bin_packing(seed=42):\n    while True:\n        pass\n")
            with self.assertRaises(self.ev.TimeoutError):
                self.ev.run_with_resource_limits(
                    hostile, timeout_seconds=2, memory_limit_mb=256
                )
        self.assertTrue(True)  # harness process survived


class TestBinPackingHasHeadroom(unittest.TestCase):
    """The C3 fix (task 0091): the benchmark measures heuristic quality."""

    @classmethod
    def setUpClass(cls):
        cls.ev = _load("bp_eval2", "evaluator.py")
        cls.initial = os.path.join(BIN_PACKING, "initial_program.py")

    def test_best_fit_leaves_real_headroom(self):
        # Online best-fit should NOT saturate the score (the offline FFD flaw).
        # ~4.6% excess on these Weibull instances → ~0.956, matching FunSearch's
        # published best-fit gap.
        score = self.ev.evaluate(self.initial)["combined_score"]
        self.assertLess(score, 0.99, "no headroom — the benchmark cannot serve C3")
        self.assertGreater(score, 0.80, "instances implausibly hard; check the generator")

    def test_score_discriminates_heuristic_quality(self):
        # A worse heuristic (worst-fit: prefer the emptiest bin) must score
        # strictly lower — proving a climbable gradient, i.e. real headroom.
        best_fit = self.ev.evaluate(self.initial)["combined_score"]
        with tempfile.TemporaryDirectory() as tmp:
            worst = _program_with_priority(
                tmp,
                "def priority(item, bins):\n"
                "    return (bins - item)  # worst-fit: emptiest bin\n",
            )
            worst_fit = self.ev.evaluate(worst)["combined_score"]
        self.assertLess(
            worst_fit, best_fit,
            "score does not respond to heuristic quality — no measurable gradient",
        )


class TestOnlinePackCapacityIndex(unittest.TestCase):
    """task 0096: online_pack's capacity-indexed rewrite must be byte-identical
    in outcome to the original O(n*bins) flat-list scan it replaced — including
    tie-breaking among equal-capacity bins, the case a naive rewrite would most
    plausibly get wrong."""

    @classmethod
    def setUpClass(cls):
        cls.ip = _load("bp_init", "initial_program.py")

    @staticmethod
    def _naive_online_pack(items, capacity, priority_fn):
        """The pre-0096 implementation, kept here only as an equivalence
        oracle — not the shipped algorithm."""
        import numpy as np

        remaining = []
        for item in items:
            fit_idx = [i for i, r in enumerate(remaining) if r >= item]
            if fit_idx:
                scores = priority_fn(item, np.array([remaining[i] for i in fit_idx], dtype=float))
                chosen = fit_idx[int(np.argmax(scores))]
                remaining[chosen] -= item
            else:
                remaining.append(capacity - item)
        return len(remaining)

    def test_matches_naive_scan_across_sizes_and_heuristics_including_ties(self):
        import numpy as np

        def worst_fit(item, bins):
            return bins - item

        def forced_tie(item, bins):
            # Every candidate scores identically -> argmax always picks the
            # first (lowest creation-index) bin. Stresses tie-break order
            # exactly where a bucket-order rewrite could silently diverge.
            return np.zeros_like(bins)

        mismatches = []
        for trial in range(50):
            rng = np.random.RandomState(trial)
            n = int(rng.choice([50, 500, 2000]))
            cap = int(rng.choice([20, 100, 500]))
            items = self.ip.generate_instance(trial, n_items=n, capacity=cap)
            for fn in (self.ip.priority, worst_fit, forced_tie):
                naive = self._naive_online_pack(items, cap, fn)
                fast = self.ip.online_pack(items, cap, fn)
                if naive != fast:
                    mismatches.append((trial, n, cap, fn.__name__, naive, fast))
        self.assertEqual(mismatches, [], f"capacity-index diverged from naive scan: {mismatches}")

    def test_scored_set_instances_unchanged_by_the_generate_instance_refactor(self):
        # generate_instance gained optional n_items/capacity params; the
        # default call (used by run_bin_packing, the search-loop entry point)
        # must still produce byte-identical instances to before.
        for seed in self.ip.INSTANCE_SEEDS:
            explicit = self.ip.generate_instance(
                seed, n_items=self.ip.N_ITEMS, capacity=self.ip.BIN_CAPACITY
            )
            default = self.ip.generate_instance(seed)
            self.assertEqual(explicit, default)


class TestHeldOutInstanceSet(unittest.TestCase):
    """task 0096: Decision #6 specifies n in {1000,5000,10000}, capacity in
    {100,500} — only n=1000/C=100 was committed, and no held-out split
    existed. This covers the gap without touching the scored (in-loop) set."""

    @classmethod
    def setUpClass(cls):
        cls.ip = _load("bp_init2", "initial_program.py")

    def test_covers_decision_6_matrix(self):
        expected = {(n, c) for n in (1000, 5000, 10000) for c in (100, 500)}
        self.assertEqual(set(self.ip.HELD_OUT_CONFIGS), expected)

    def test_held_out_seeds_disjoint_from_scored_seeds(self):
        self.assertEqual(set(self.ip.HELD_OUT_SEEDS) & set(self.ip.INSTANCE_SEEDS), set())

    def test_held_out_run_produces_valid_results_at_every_config(self):
        results = self.ip.run_bin_packing_held_out()
        self.assertEqual(set(results.keys()), set(self.ip.HELD_OUT_CONFIGS))
        for (n_items, capacity), config_results in results.items():
            self.assertEqual(len(config_results), len(self.ip.HELD_OUT_SEEDS))
            for bins_used, lower_bound in config_results:
                self.assertGreater(lower_bound, 0)
                self.assertGreaterEqual(bins_used, lower_bound)

    def test_largest_instance_evaluates_well_within_the_evaluator_timeout(self):
        # The ticket's explicit ask: verify n=10000 doesn't approach the
        # evaluator's 60s timeout. online_pack's capacity-index makes this
        # comfortable even at the largest committed size (measured ~0.1-0.2s
        # per instance vs ~1-1.3s pre-0096 -- both fine, but the margin now
        # holds for future, larger held-out configs too).
        import time

        items = self.ip.generate_instance(6, n_items=10000, capacity=500)
        start = time.time()
        self.ip.online_pack(items, 500, self.ip.priority)
        elapsed = time.time() - start
        self.assertLess(elapsed, 10.0, f"n=10000 took {elapsed:.2f}s -- investigate before trusting the 60s margin")


class TestBinPackingRunsInTheController(unittest.TestCase):
    """0036 done-when 1: the noema loop completes on the real benchmark and
    writes a run dir — end to end, evaluating through the subprocess harness."""

    def test_null_arm_stub_run_completes_and_writes_a_run_dir(self):
        import asyncio
        from types import SimpleNamespace

        from openevolve.config import DatabaseConfig, EvaluatorConfig
        from noema.budget.ledger import TokenLedger
        from noema.budget.llm import BudgetedLLM
        from noema.config import BudgetConfig, CoordinationConfig, NoemaConfig
        from noema.controller import NoemaController
        from noema.coordination import NullCoordination

        with open(os.path.join(BIN_PACKING, "initial_program.py")) as f:
            program_src = f.read()

        # Fake client returns the program verbatim as a full rewrite — a valid
        # no-op "mutation" that evaluates to the baseline. Exercises the whole
        # loop (prompt -> parse -> subprocess evaluate -> add -> checkpoint).
        class _Client:
            def __init__(self):
                async def create(**params):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(
                            content=f"```python\n{program_src}\n```"))],
                        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                    )
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        with tempfile.TemporaryDirectory() as tmp:
            config = NoemaConfig(
                max_iterations=2,
                checkpoint_interval=100,
                diff_based_evolution=False,
                database=DatabaseConfig(in_memory=True, num_islands=2,
                                        random_seed=42, migration_interval=1000),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=90),
                budget=BudgetConfig(total_tokens=1_000_000),
                coordination=CoordinationConfig(module="null"),
            )
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            llm = BudgetedLLM(model="fake", ledger=ledger, account="mutation",
                              tag="mutate", client=_Client(), retries=0, retry_delay=0.0)
            controller = NoemaController(
                config=config,
                evaluation_file=os.path.join(BIN_PACKING, "evaluator.py"),
                initial_program_code=program_src,
                output_dir=os.path.join(tmp, "out"),
                mutation_llm=llm,
                coordination=NullCoordination(),
                ledger=ledger,
            )
            asyncio.run(controller.run(iterations=2))

            # Children were produced and scored on the real benchmark.
            self.assertGreater(controller.db.num_programs, 1)
            best = controller.db.best_program()
            self.assertIsNotNone(best)
            self.assertGreater(best.metrics["combined_score"], 0.0)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "out")))


if __name__ == "__main__":
    unittest.main()
