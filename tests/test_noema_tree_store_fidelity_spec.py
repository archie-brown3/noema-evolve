"""Deferred UCT behaviour contracts for task 0037.

TreeStore storage and topology are ordinary green tests in
``tests.test_noema_tree_store``.  This file now owns selection behaviour only:
the published kernel equations, neutral runtime selection, and split
store/policy checkpoint continuation.
"""

from __future__ import annotations

import importlib
import json
import unittest

from openevolve.database import Program


def _uct_module(testcase):
    try:
        return importlib.import_module("noema.selection.uct")
    except ImportError as exc:
        testcase.fail(f"missing planned UCT policy module: {exc}")


def _program(program_id, fitness, parent_id=None):
    return Program(
        id=program_id,
        code=f"def {program_id}():\n    return {fitness}\n",
        language="python",
        metrics={"combined_score": fitness},
        parent_id=parent_id,
    )


class TestMctsAhdKernelSpec(unittest.TestCase):
    def test_min_max_normalized_uct_matches_equation_five(self):
        module = _uct_module(self)
        import math

        expected = 0.5 + 0.1 * math.sqrt(math.log(15 + 1) / 3)
        self.assertAlmostEqual(
            module.uct_score(
                quality=4.0,
                min_quality=2.0,
                max_quality=6.0,
                parent_visits=15,
                child_visits=3,
                exploration=0.1,
            ),
            expected,
        )

    def test_progressive_widening_matches_equation_four(self):
        module = _uct_module(self)
        self.assertFalse(module.should_widen(visits=8, child_count=3, alpha=0.5))
        self.assertTrue(module.should_widen(visits=9, child_count=3, alpha=0.5))

    def test_exploration_decay_uses_tokens_not_evaluation_count(self):
        module = _uct_module(self)
        self.assertAlmostEqual(
            module.budget_exploration(
                initial=0.1, tokens_spent=250, token_budget=1000
            ),
            0.075,
        )
        self.assertEqual(
            module.budget_exploration(
                initial=0.1, tokens_spent=1000, token_budget=1000
            ),
            0.0,
        )


class TestUCTPolicyHostAdaptationSpec(unittest.TestCase):
    def make_runtime(self, seed=7):
        module = _uct_module(self)
        base = importlib.import_module("noema.base")
        tree = importlib.import_module("noema.tree")
        store = tree.TreeStore(steps_per_generation=4)
        policy = module.UCTSelectionPolicy(
            token_budget=10_000,
            initial_exploration=0.1,
            random_seed=seed,
        )
        return base.SubstrateRuntime(store, policy)

    def test_runtime_selection_uses_neutral_selection_value(self):
        base = importlib.import_module("noema.base")
        runtime = self.make_runtime()
        runtime.store.add(_program("p0", 1.0))

        selected = runtime.select(target_scope=None, num_inspirations=0)

        self.assertIsInstance(selected, base.Selection)
        self.assertEqual(selected.parent.id, "p0")
        self.assertIsNone(selected.source_scope)
        self.assertIsNone(selected.target_scope)

    def test_checkpoint_resume_preserves_the_selection_trace(self):
        runtime = self.make_runtime(seed=123)
        for index, fitness in enumerate((1.0, 0.5, 1.3, 0.9)):
            runtime.store.add(
                _program(
                    f"p{index}",
                    fitness,
                    None if index == 0 else "p0",
                )
            )

        for _ in range(3):
            runtime.select(target_scope=None, num_inspirations=0)
        state = json.loads(
            json.dumps(
                {
                    "store": runtime.store.state_dict(),
                    "runtime": runtime.state_dict(),
                }
            )
        )
        expected = [
            runtime.select(target_scope=None, num_inspirations=0).parent.id
            for _ in range(8)
        ]

        resumed = self.make_runtime(seed=999)
        resumed.store.load_state_dict(state["store"])
        resumed.load_state_dict(state["runtime"])
        actual = [
            resumed.select(target_scope=None, num_inspirations=0).parent.id
            for _ in range(8)
        ]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
