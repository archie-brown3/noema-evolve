"""Behavioural specification for MCTS-AHD tree topology + UCT selection.

The population-store seam is task 0074; the concrete tree is task 0037.  These
tests pin the scientific behavior before either implementation lands.  TreeStore
owns topology/storage; UCTSelectionPolicy owns selection and visit state; callers
compose them through SubstrateRuntime and see only neutral Selection and
PopulationSnapshot values.  The kernel helpers keep the borrowed equations
directly auditable.
"""

from __future__ import annotations

import importlib
import json
import unittest


def _module(testcase):
    try:
        return importlib.import_module("noema.substrate.tree")
    except ImportError as exc:
        testcase.fail(f"missing planned TreeStore module: {exc}")


class TestMctsAhdKernelSpec(unittest.TestCase):
    @unittest.expectedFailure
    def test_min_max_normalized_uct_matches_equation_five(self):
        module = _module(self)
        # Q=4 in a sibling range [2, 6], N(parent)=15, N(child)=3, lambda=.1.
        # Spell the published expression independently of the implementation.
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

    @unittest.expectedFailure
    def test_progressive_widening_matches_equation_four(self):
        module = _module(self)
        # floor(sqrt(8))=2 and floor(sqrt(9))=3.
        self.assertFalse(module.should_widen(visits=8, child_count=3, alpha=0.5))
        self.assertTrue(module.should_widen(visits=9, child_count=3, alpha=0.5))

    @unittest.expectedFailure
    def test_exploration_decay_uses_tokens_not_evaluation_count(self):
        module = _module(self)
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


class TestTreeStoreHostAdaptationSpec(unittest.TestCase):
    def make_runtime(self, seed=7):
        module = _module(self)
        base = importlib.import_module("noema.substrate.base")
        store = module.TreeStore(steps_per_generation=4)
        policy = module.UCTSelectionPolicy(
            token_budget=10_000,
            initial_exploration=0.1,
            random_seed=seed,
        )
        return base.SubstrateRuntime(store, policy)

    @unittest.expectedFailure
    def test_virtual_root_is_not_exposed_as_a_program(self):
        store = self.make_runtime().store
        snapshot = store.snapshot(scope=None)
        self.assertEqual(snapshot.top_programs, ())
        self.assertIsNone(snapshot.best_program)

    @unittest.expectedFailure
    def test_one_accepted_child_is_inserted_per_host_iteration(self):
        store = self.make_runtime().store
        root_child = {"id": "p0", "fitness": 1.0, "parent_id": None}
        store.add(root_child, target_scope=None)
        before = store.snapshot(scope=None)

        child = {"id": "p1", "fitness": 1.2, "parent_id": "p0"}
        store.add(child, target_scope=None)
        after = store.snapshot(scope=None)

        self.assertEqual(len(after.top_programs), len(before.top_programs) + 1)
        self.assertEqual(after.best_program, child)

    @unittest.expectedFailure
    def test_runtime_selection_uses_neutral_selection_value(self):
        base = importlib.import_module("noema.substrate.base")
        runtime = self.make_runtime()
        store = runtime.store
        store.add({"id": "p0", "fitness": 1.0, "parent_id": None})

        selected = runtime.select(target_scope=None, num_inspirations=0)

        self.assertIsInstance(selected, base.Selection)
        self.assertEqual(selected.parent["id"], "p0")
        self.assertIsNone(selected.source_scope)
        self.assertIsNone(selected.target_scope)

    @unittest.expectedFailure
    def test_checkpoint_resume_preserves_the_selection_trace(self):
        runtime = self.make_runtime(seed=123)
        store = runtime.store
        for index, fitness in enumerate((1.0, 0.5, 1.3, 0.9)):
            store.add(
                {
                    "id": f"p{index}",
                    "fitness": fitness,
                    "parent_id": None if index == 0 else "p0",
                }
            )

        for _ in range(3):
            runtime.select(target_scope=None, num_inspirations=0)
        state = json.loads(
            json.dumps({"store": store.state_dict(), "runtime": runtime.state_dict()})
        )
        expected = [
            runtime.select(target_scope=None, num_inspirations=0).parent["id"]
            for _ in range(8)
        ]

        resumed = self.make_runtime(seed=999)
        resumed.store.load_state_dict(state["store"])
        resumed.load_state_dict(state["runtime"])
        actual = [
            resumed.select(target_scope=None, num_inspirations=0).parent["id"]
            for _ in range(8)
        ]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
