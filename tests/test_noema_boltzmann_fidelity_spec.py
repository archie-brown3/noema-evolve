"""Red executable specification for LoongFlow-compatible Boltzmann selection.

The donor is the Apache-2.0 ``loongflow==0.0.1`` wheel, SHA-256
``cdc0bc9b9f6339e4517ffc6847040de4681da8d992d41da4b33642eb53ce2493``.
Tests distinguish released kernel behavior from noema host adaptations.  They
fail until the policy exists, but imports are lazy so discovery still works.
"""

from __future__ import annotations

import importlib
import json
import unittest
from dataclasses import dataclass

import numpy as np


def _module(testcase):
    try:
        return importlib.import_module("noema.selection.boltzmann")
    except ImportError as exc:
        testcase.fail(f"missing planned Boltzmann policy module: {exc}")


@dataclass(eq=True)
class Candidate:
    solution: str
    solution_id: str
    score: float
    sample_weight: float = 0.0
    sample_cnt: int = 0


def _population():
    return [
        Candidate(
            solution=f"line {index}\n" * (index + 1),
            solution_id=f"p{index}",
            score=float(index),
            sample_weight=float(index + 1),
        )
        for index in range(10)
    ]


class TestReleasedBoltzmannKernelSpec(unittest.TestCase):
    def test_seeded_trace_matches_released_wheel(self):
        module = _module(self)
        population = _population()
        elites = population[:5]
        rng = np.random.RandomState(42)
        trace = [
            module.select_parents_with_dynamic_temperature(
                population,
                elites,
                initial_temp=1.0,
                exploration_rate=0.0,
                rng=rng,
            ).solution_id
            for _ in range(5)
        ]
        self.assertEqual(trace, ["p3", "p8", "p8", "p3", "p4"])

    def test_temperature_formula_matches_released_wheel(self):
        module = _module(self)
        observed = [
            module._adaptive_temperature_by_diversity(1.0, diversity)
            for diversity in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]
        for actual, expected in zip(observed, (0.6, 0.6, 1.0, 1.4, 1.8)):
            self.assertAlmostEqual(actual, expected)

    def test_diversity_formula_matches_released_wheel(self):
        module = _module(self)
        first = Candidate("ab\n", "a", 0.0)
        second = Candidate("abc\ndef\n", "b", 0.0)
        expected = 0.4 * (5 / 8) + 0.3 * (1 / 2) + 0.3 * (4 / 7)
        observed = module._calculate_diversity(
            [first, second], rng=np.random.RandomState(0)
        )
        self.assertAlmostEqual(observed, expected)

    def test_sampling_weight_multiplies_stable_boltzmann_probability(self):
        module = _module(self)
        population = [
            Candidate("a", "a", 10_000.0, sample_weight=1.0),
            Candidate("b", "b", 9_998.0, sample_weight=3.0),
        ]
        probabilities = module._combined_probabilities(
            population, temperature=2.0, use_sampling_weight=True
        )
        expected_ratio = 3.0 * np.exp(-1.0)
        self.assertAlmostEqual(probabilities[1] / probabilities[0], expected_ratio)
        self.assertAlmostEqual(float(np.sum(probabilities)), 1.0)
        self.assertTrue(np.all(np.isfinite(probabilities)))

    def test_released_elite_membership_quirk_is_pinned(self):
        module = _module(self)
        population = _population()
        elites = population[:5]
        candidates = module._candidate_pool_released(
            population, elites, rng=np.random.RandomState(7)
        )
        # Released 0.0.1 compares ``solution.solution_id`` with a list of
        # Solution objects, so elite solutions also enter the non-elite pool.
        self.assertEqual(
            [candidate.solution_id for candidate in candidates],
            ["p0", "p3", "p2", "p9", "p6"],
        )


class TestReleasedStagnationSpec(unittest.TestCase):
    def test_recent_history_means_five_solutions_and_four_deltas(self):
        module = _module(self)
        self.assertEqual(
            module._stagnation_adjusted_exploration_rate(
                0.2, [1.0, 1.005, 1.009, 1.010, 1.011], mode="released"
            ),
            0.4,
        )

    def test_released_hard_branch_is_unreachable(self):
        module = _module(self)
        # The released code checks <0.01 before ``elif <0.001``.  Exact-release
        # fidelity therefore yields x2, not the comment's intended x4.
        self.assertEqual(
            module._stagnation_adjusted_exploration_rate(
                0.2, [1.0, 1.0001, 1.0002, 1.0003, 1.0004], mode="released"
            ),
            0.4,
        )

    def test_released_empty_history_and_cap_behavior_are_pinned(self):
        module = _module(self)
        self.assertEqual(
            module._stagnation_adjusted_exploration_rate(0.2, [], mode="released"),
            0.4,
        )
        self.assertEqual(
            module._stagnation_adjusted_exploration_rate(0.6, [], mode="released"),
            0.9,
        )


class TestNoemaBoltzmannPolicyHostSpec(unittest.TestCase):
    def make_policy(self, seed=7):
        module = _module(self)
        return module.BoltzmannSelectionPolicy(
            rng=np.random.RandomState(seed),
            temperature=1.0,
            exploration_rate=0.2,
            stagnation_mode="released",
        )

    def test_empty_target_uses_noema_global_bootstrap_fallback(self):
        policy = self.make_policy()
        fallback = _population()[:3]
        selected = policy.select(
            [], fallback=fallback, elites=[], num_inspirations=2
        )
        self.assertIn(selected.parent, fallback)
        self.assertEqual(len(selected.inspirations), 2)
        self.assertNotIn(selected.parent, selected.inspirations)

    def test_inspirations_are_uniform_without_replacement(self):
        policy = self.make_policy()
        selected = policy.select(
            _population(), fallback=_population(), elites=[], num_inspirations=3
        )
        self.assertEqual(len({item.solution_id for item in selected.inspirations}), 3)
        self.assertNotIn(selected.parent, selected.inspirations)
        self.assertEqual(policy.weighted_inspiration_draws, 0)

    def test_accepted_child_applies_verified_heredity_formula_once(self):
        policy = self.make_policy()
        parent = Candidate("parent", "parent", 0.4, sample_weight=1.25, sample_cnt=7)
        child = Candidate("child", "child", 0.9)
        policy.on_child_accepted(parent=parent, child=child, step_size=0.5)
        expected = max(0.05, 1.25 + 3 * (0.9 - 0.4) * 0.5 + 3 * 0.9)
        self.assertAlmostEqual(child.sample_weight, expected)
        self.assertEqual(parent.sample_cnt, 8)
        self.assertEqual(child.sample_cnt, 0)

    def test_failed_or_rejected_attempt_does_not_update_heredity(self):
        policy = self.make_policy()
        parent = Candidate("parent", "parent", 0.4, sample_weight=1.25, sample_cnt=7)
        child = Candidate("child", "child", 0.9)
        policy.on_child_rejected(parent=parent, child=child, eval_failed=True)
        self.assertEqual(parent.sample_cnt, 7)
        self.assertEqual(child.sample_weight, 0.0)

    def test_state_is_json_safe_and_resume_trace_is_identical(self):
        population = _population()

        def pull(policy):
            selected = policy.select(
                population, fallback=population, elites=population[:5], num_inspirations=2
            )
            return (
                selected.parent.solution_id,
                tuple(item.solution_id for item in selected.inspirations),
            )

        uninterrupted = self.make_policy(seed=1234)
        for _ in range(5):
            pull(uninterrupted)
        state = json.loads(json.dumps(uninterrupted.state_dict()))
        expected = [pull(uninterrupted) for _ in range(10)]

        resumed = self.make_policy(seed=0)
        resumed.load_state_dict(state)
        self.assertEqual([pull(resumed) for _ in range(10)], expected)


if __name__ == "__main__":
    unittest.main()
