"""
Acceptance tests for the bin-packing benchmark harness (task 0036).

These verify the *mechanics* the ticket's done-when calls for: the evaluator
subprocesses candidate programs with a timeout (the Evaluator is not a sandbox),
scores are deterministic, and the role-structured initial program evaluates to a
valid solution.

They deliberately do NOT assert the benchmark has evolutionary headroom — it does
not, and that is a separate finding (see the module-level note below and the
follow-up ticket): the initial program is offline First-Fit-Decreasing, which is
within 11/9 of optimal and here hits the material lower bound exactly (score 1.0),
so there is nothing for evolution to improve. That is a benchmark-validity issue
for C3, tracked separately; the harness itself is sound and is what these cover.
"""

import importlib.util
import os
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_PACKING = os.path.join(REPO, "examples", "bin_packing")


def _load_evaluator():
    spec = importlib.util.spec_from_file_location(
        "bin_packing_evaluator", os.path.join(BIN_PACKING, "evaluator.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBinPackingHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = _load_evaluator()
        cls.initial = os.path.join(BIN_PACKING, "initial_program.py")

    def test_initial_program_evaluates_to_a_valid_solution(self):
        result = self.ev.evaluate(self.initial)
        self.assertEqual(result["validity"], 1.0)
        self.assertGreaterEqual(result["combined_score"], 0.0)
        self.assertLessEqual(result["combined_score"], 1.0)
        # bins_used never below the material lower bound (a real packing).
        self.assertGreaterEqual(result["bins_used"], result["lower_bound"])

    def test_scoring_is_deterministic(self):
        # Done-when: two evaluations of the same program give identical scores.
        a = self.ev.evaluate(self.initial)
        b = self.ev.evaluate(self.initial)
        self.assertEqual(a["combined_score"], b["combined_score"])
        self.assertEqual(a["bins_used"], b["bins_used"])

    def test_hostile_program_times_out_without_killing_the_harness(self):
        # Done-when: the evaluator subprocesses, so a runaway candidate is bounded
        # by the timeout and the harness survives.
        with tempfile.TemporaryDirectory() as tmp:
            hostile = os.path.join(tmp, "hostile.py")
            with open(hostile, "w") as f:
                f.write("while True:\n    pass\n")
            with self.assertRaises((TimeoutError, Exception)) as cm:
                self.ev.run_with_resource_limits(
                    hostile, timeout_seconds=2, memory_limit_mb=256
                )
            # It was a timeout, not an import/other error.
            self.assertIn("time", str(cm.exception).lower())
        # If we got here, the harness process itself is still alive.
        self.assertTrue(True)


class TestBinPackingHeadroomFinding(unittest.TestCase):
    """Documents the C3 benchmark-validity finding as an executable fact.

    The offline FFD initial program already achieves the material lower bound, so
    the benchmark has no headroom. This test PINS that state so that when the
    benchmark is redesigned (online, Weibull instances per Decision #6, scoring
    vs a published baseline) this assertion must be revisited — it is a tripwire,
    not an endorsement.
    """

    def test_initial_program_currently_maxes_the_score_no_headroom(self):
        ev = _load_evaluator()
        result = ev.evaluate(os.path.join(BIN_PACKING, "initial_program.py"))
        self.assertEqual(
            result["combined_score"],
            1.0,
            "If this changed, the bin-packing benchmark may now have headroom — "
            "revisit the C3 redesign follow-up and update this tripwire.",
        )


if __name__ == "__main__":
    unittest.main()
