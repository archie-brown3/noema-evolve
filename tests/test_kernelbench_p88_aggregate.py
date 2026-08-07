"""fast_p aggregation tests (task 0112, spec §16 items 11-15)."""

import unittest

from examples.kernelbench_coordination_smoke.aggregate import (
    DRIFT_FLAG_THRESHOLD,
    MANDATORY_DISCLAIMER,
    AggregationError,
    ArmRunSummary,
    aggregate,
    fast_p_for,
)
from examples.kernelbench_coordination_smoke.worker_protocol import (
    make_correct_result,
    make_failed_result,
)

FINGERPRINT = {"problem_sha256": "p88", "kernelbench_commit": "423217d", "model": "x", "seed": 42}


def summary(arm, *, confirmed=None, screening_speedup=None, attempted=10, compiled=8, correct=4,
           mutation_tokens=40000, coordination_tokens=0, fingerprint=None, **overrides):
    kwargs = dict(
        arm=arm, invariant_fingerprint=fingerprint or FINGERPRINT, confirmed=confirmed,
        screening_speedup=screening_speedup, attempted=attempted, compiled=compiled,
        correct=correct, tokens_to_first_compile=100, tokens_to_first_correct=200,
        tokens_to_parity=300, tokens_to_confirmed_parity=350,
        mutation_tokens=mutation_tokens, coordination_tokens=coordination_tokens,
        operator_pulls={"e1": 3, "m1": 2},
    )
    kwargs.update(overrides)
    return ArmRunSummary(**kwargs)


class TestFastPThresholds(unittest.TestCase):
    def test_strict_greater_than_at_exact_boundaries(self):
        # spec §16 item 12: strict '>' at exact threshold boundaries.
        exact_2x = make_correct_result("x", speedup=2.0)
        fp = fast_p_for(exact_2x)
        self.assertTrue(fp[0.0] and fp[1.0] and fp[1.5])
        self.assertFalse(fp[2.0])  # 2.0 is NOT > 2.0

    def test_just_above_boundary_passes(self):
        just_above = make_correct_result("x", speedup=2.0001)
        self.assertTrue(fast_p_for(just_above)[2.0])

    def test_none_confirmed_zeros_every_threshold(self):
        fp = fast_p_for(None)
        self.assertTrue(all(v is False for v in fp.values()))

    def test_incorrect_confirmed_zeros_every_threshold(self):
        # A WorkerResult can only be non-correct with correct=False by
        # construction; simulate via make_failed_result.
        fp = fast_p_for(make_failed_result("x", "wrong_answer"))
        self.assertTrue(all(v is False for v in fp.values()))


class TestAggregation(unittest.TestCase):
    def test_disclaimer_is_always_present(self):
        report = aggregate([summary("null", confirmed=make_correct_result("x", speedup=1.0))])
        self.assertEqual(report.disclaimer, MANDATORY_DISCLAIMER)

    def test_empty_input_refused(self):
        with self.assertRaises(AggregationError):
            aggregate([])

    def test_invariant_mismatch_refused(self):
        # spec §16 item 14: invariant mismatch blocks aggregation entirely.
        s1 = summary("null", confirmed=make_correct_result("a", speedup=1.0))
        s2 = summary("hifo", confirmed=make_correct_result("b", speedup=1.0),
                     fingerprint={**FINGERPRINT, "model": "DIFFERENT"})
        with self.assertRaises(AggregationError):
            aggregate([s1, s2])

    def test_failed_confirmation_zeros_fast_p_for_that_arm_only(self):
        # spec §16 item 13.
        good = summary("null", confirmed=make_correct_result("a", speedup=3.0), screening_speedup=3.0)
        bad = summary("hifo", confirmed=make_failed_result("b", "wrong_answer"))
        report = aggregate([good, bad])
        self.assertTrue(report.arms["null"].fast_p[2.0])
        self.assertTrue(all(v is False for v in report.arms["hifo"].fast_p.values()))
        self.assertTrue(report.arms["hifo"].confirmation_failed)
        self.assertFalse(report.arms["null"].confirmation_failed)

    def test_never_confirmed_arm_is_distinct_from_failed_confirmation(self):
        never_ran = summary("bandit", confirmed=None)
        report = aggregate([never_ran])
        self.assertFalse(report.arms["bandit"].confirmation_failed)  # None != failed
        self.assertTrue(all(v is False for v in report.arms["bandit"].fast_p.values()))

    def test_confirmation_drift_computed_and_flagged(self):
        confirmed = make_correct_result("a", speedup=1.10)
        s = summary("null", confirmed=confirmed, screening_speedup=1.00)
        report = aggregate([s])
        self.assertAlmostEqual(report.arms["null"].confirmation_drift, 0.10)
        self.assertTrue(report.arms["null"].confirmation_drift_flagged)  # > 5%

    def test_small_drift_not_flagged(self):
        confirmed = make_correct_result("a", speedup=1.02)
        s = summary("null", confirmed=confirmed, screening_speedup=1.00)
        report = aggregate([s])
        self.assertLess(report.arms["null"].confirmation_drift, DRIFT_FLAG_THRESHOLD)
        self.assertFalse(report.arms["null"].confirmation_drift_flagged)

    def test_compile_and_correct_rates(self):
        s = summary("null", attempted=20, compiled=15, correct=5,
                    confirmed=make_correct_result("a", speedup=1.0))
        report = aggregate([s])
        self.assertAlmostEqual(report.arms["null"].compile_rate, 0.75)
        self.assertAlmostEqual(report.arms["null"].correct_rate, 0.25)

    def test_zero_attempts_gives_zero_rates_not_a_crash(self):
        s = summary("null", attempted=0, compiled=0, correct=0, confirmed=None)
        report = aggregate([s])
        self.assertEqual(report.arms["null"].compile_rate, 0.0)
        self.assertEqual(report.arms["null"].correct_rate, 0.0)

    def test_token_accounting_separates_mutation_and_coordination(self):
        s = summary("hifo", mutation_tokens=30000, coordination_tokens=15000,
                    confirmed=make_correct_result("a", speedup=1.0))
        report = aggregate([s])
        self.assertEqual(report.arms["hifo"].mutation_tokens, 30000)
        self.assertEqual(report.arms["hifo"].coordination_tokens, 15000)
        self.assertEqual(report.arms["hifo"].total_tokens, 45000)

    def test_excessive_and_suspicious_speedup_flags(self):
        excessive = summary("null", confirmed=make_correct_result("a", speedup=15.0))
        suspicious = summary("hifo", confirmed=make_correct_result("b", speedup=3.0))
        normal = summary("pes-faithful", confirmed=make_correct_result("c", speedup=1.2))
        report = aggregate([excessive, suspicious, normal])
        self.assertTrue(report.arms["null"].excessive_speedup_flag)
        self.assertTrue(report.arms["hifo"].suspicious_speedup_flag)
        self.assertFalse(report.arms["hifo"].excessive_speedup_flag)
        self.assertFalse(report.arms["pes-faithful"].suspicious_speedup_flag)

    def test_operator_pulls_carried_through_for_bandit(self):
        s = summary("bandit", confirmed=make_correct_result("a", speedup=1.0),
                    operator_pulls={"e1": 10, "e2": 3, "m1": 7})
        report = aggregate([s])
        self.assertEqual(report.arms["bandit"].operator_pulls, {"e1": 10, "e2": 3, "m1": 7})

    def test_invalid_counts_rejected_at_construction(self):
        with self.assertRaises(AggregationError):
            summary("null", attempted=5, compiled=10, correct=0)  # compiled > attempted
        with self.assertRaises(AggregationError):
            summary("null", attempted=10, compiled=5, correct=8)  # correct > compiled

    def test_four_arm_realistic_report(self):
        # The actual shape a real 0104 run would produce: full smoke matrix.
        summaries = [
            summary("null", confirmed=make_correct_result("n", speedup=1.1), screening_speedup=1.1,
                    mutation_tokens=50000, coordination_tokens=0),
            summary("hifo", confirmed=make_correct_result("h", speedup=1.3), screening_speedup=1.3,
                    mutation_tokens=42000, coordination_tokens=8000),
            summary("pes-faithful", confirmed=None, mutation_tokens=38000, coordination_tokens=12000),
            summary("bandit", confirmed=make_failed_result("b", "timeout"),
                    mutation_tokens=50000, coordination_tokens=0),
        ]
        report = aggregate(summaries)
        self.assertEqual(set(report.arms), {"null", "hifo", "pes-faithful", "bandit"})
        self.assertTrue(report.arms["null"].fast_p[1.0])
        self.assertTrue(report.arms["hifo"].fast_p[1.0])
        self.assertFalse(report.arms["pes-faithful"].fast_p[0.0])  # never confirmed
        self.assertFalse(report.arms["bandit"].fast_p[0.0])  # confirmation failed


if __name__ == "__main__":
    unittest.main()
