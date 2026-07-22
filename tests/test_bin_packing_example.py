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
