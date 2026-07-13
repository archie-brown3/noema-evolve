"""UCT policy, composition, and validity tests for task 0037."""

from __future__ import annotations

import inspect
import json
import math
import unittest

from openevolve.config import DatabaseConfig, PromptConfig
from openevolve.database import Program

from noema.config import NoemaConfig, SelectionConfig, SubstrateConfig
from noema.islands import IslandsStore
from noema.prompts import build_mutation_prompt, make_prompt_sampler
from noema.registry import build_substrate_runtime
from noema.selection.uct import (
    UCTSelectionPolicy,
    budget_exploration,
    should_widen,
    uct_score,
)
from noema.tree import TreeStore


def program(program_id: str, score: float, parent_id=None) -> Program:
    return Program(
        id=program_id,
        code=f"def f():\n    return {score}\n",
        language="python",
        parent_id=parent_id,
        metrics={"combined_score": score},
    )


def policy(**overrides) -> UCTSelectionPolicy:
    options = dict(
        token_budget=1_000,
        initial_exploration=0.1,
        widening_alpha=0.5,
        random_seed=7,
    )
    options.update(overrides)
    return UCTSelectionPolicy(**options)


def accept(store, selector, child_id: str, score: float):
    selection = selector.select(store, target_scope=None, num_inspirations=0)
    child = program(child_id, score, selection.parent.id)
    selector.on_child_accepted(parent=selection.parent, child=child, step_size=0.5)
    store.add(child)
    return selection.parent.id


class TestUCTKernel(unittest.TestCase):
    def test_hand_computed_equation_and_equal_quality_case(self):
        expected = 0.5 + 0.1 * math.sqrt(math.log(16) / 3)
        self.assertAlmostEqual(
            uct_score(
                quality=4,
                min_quality=2,
                max_quality=6,
                parent_visits=15,
                child_visits=3,
                exploration=0.1,
            ),
            expected,
        )
        self.assertEqual(
            uct_score(
                quality=2,
                min_quality=2,
                max_quality=2,
                parent_visits=0,
                child_visits=1,
                exploration=0.1,
            ),
            0.0,
        )

    def test_widening_and_token_decay_boundaries(self):
        self.assertFalse(should_widen(visits=8, child_count=3, alpha=0.5))
        self.assertTrue(should_widen(visits=9, child_count=3, alpha=0.5))
        self.assertAlmostEqual(
            budget_exploration(initial=0.1, tokens_spent=250, token_budget=1000),
            0.075,
        )
        self.assertEqual(
            budget_exploration(initial=0.1, tokens_spent=1000, token_budget=1000),
            0.0,
        )
        self.assertEqual(
            budget_exploration(initial=0.1, tokens_spent=2000, token_budget=1000),
            0.0,
        )

    def test_invalid_kernel_domains_fail(self):
        with self.assertRaises(ValueError):
            uct_score(
                quality=1,
                min_quality=0,
                max_quality=1,
                parent_visits=1,
                child_visits=0,
                exploration=0.1,
            )
        with self.assertRaises(ValueError):
            should_widen(visits=-1, child_count=0, alpha=0.5)
        with self.assertRaises(ValueError):
            budget_exploration(initial=float("nan"), tokens_spent=0, token_budget=1)


class TestUCTTraversalAndLifecycle(unittest.TestCase):
    def test_uct_descends_to_best_child_when_widening_is_closed(self):
        store = TreeStore()
        store.add(program("seed", 0.0))
        store.add(program("alpha", 1.0, "seed"))
        store.add(program("beta", 2.0, "seed"))

        selected = policy(initial_exploration=0).select(
            store, target_scope=None, num_inspirations=0
        )
        self.assertEqual(selected.parent.id, "beta")

    def test_progressive_widening_changes_actual_traversal(self):
        store = TreeStore()
        for item in (
            program("seed", 0.0),
            program("alpha", 1.0, "seed"),
            program("beta", 2.0, "seed"),
            program("a1", 1.1, "alpha"),
            program("a2", 1.2, "alpha"),
            program("b1", 2.1, "beta"),
            program("b2", 2.2, "beta"),
        ):
            store.add(item)

        selector = policy(initial_exploration=0)
        selected = selector.select(store, target_scope=None, num_inspirations=0)

        self.assertEqual(selector.visits["seed"], 4)
        self.assertTrue(should_widen(visits=4, child_count=2, alpha=0.5))
        self.assertEqual(selected.parent.id, "seed")

    def test_accepted_children_backpropagate_max_quality_and_sum_visits(self):
        store = TreeStore()
        store.add(program("seed", 0.0))
        selector = policy(initial_exploration=0)

        self.assertEqual(accept(store, selector, "alpha", 1.0), "seed")
        self.assertEqual(selector.qualities["seed"], 1.0)
        self.assertEqual(selector.visits["seed"], 1)

        self.assertEqual(accept(store, selector, "beta", 3.0), "seed")
        self.assertEqual(selector.qualities["seed"], 3.0)
        self.assertEqual(selector.visits["seed"], 2)

        self.assertEqual(accept(store, selector, "beta-child", 4.0), "beta")
        self.assertEqual(selector.qualities["beta"], 4.0)
        self.assertEqual(selector.visits["beta"], 1)
        self.assertEqual(selector.qualities["seed"], 4.0)
        self.assertEqual(selector.visits["seed"], 2)

    def test_rejection_clears_pending_path_without_inventing_a_visit(self):
        store = TreeStore()
        seed = program("seed", 1.0)
        store.add(seed)
        selector = policy()
        selector.select(store, target_scope=None, num_inspirations=0)
        before = (dict(selector.visits), dict(selector.qualities))

        selector.on_child_rejected(parent=seed, child=None, eval_failed=True)

        self.assertEqual((selector.visits, selector.qualities), before)
        self.assertEqual(selector.state_dict()["pending_path"], [])

    def test_inspirations_use_bounded_store_context_and_exclude_parent(self):
        store = TreeStore(working_set_size=3)
        store.add(program("seed", 0.0))
        store.add(program("alpha", 1.0, "seed"))
        store.add(program("beta", 2.0, "seed"))

        selected = policy(initial_exploration=0).select(
            store, target_scope=None, num_inspirations=2
        )
        self.assertEqual(selected.parent.id, "beta")
        self.assertEqual([item.id for item in selected.inspirations], ["alpha", "seed"])
        self.assertIsNone(selected.source_scope)
        self.assertIsNone(selected.target_scope)


class TestUCTCheckpointState(unittest.TestCase):
    def test_json_round_trip_preserves_pending_and_continuation(self):
        store = TreeStore()
        store.add(program("seed", 0.0))
        selector = policy()
        pending = selector.select(store, target_scope=None, num_inspirations=0)
        state = json.loads(json.dumps(selector.state_dict()))

        resumed = policy(random_seed=999)
        resumed.load_state_dict(state)
        child = program("alpha", 2.0, pending.parent.id)
        selector.on_child_accepted(parent=pending.parent, child=child, step_size=0.5)
        resumed.on_child_accepted(parent=pending.parent, child=child, step_size=0.5)

        self.assertEqual(resumed.state_dict(), selector.state_dict())

    def test_split_store_policy_checkpoint_has_same_next_parent(self):
        store = TreeStore()
        store.add(program("seed", 0.0))
        selector = policy()
        accept(store, selector, "alpha", 1.0)
        accept(store, selector, "beta", 2.0)
        state = json.loads(
            json.dumps({"store": store.state_dict(), "policy": selector.state_dict()})
        )

        restored_store = TreeStore()
        restored_store.load_state_dict(state["store"])
        restored_policy = policy(random_seed=999)
        restored_policy.load_state_dict(state["policy"])

        expected = selector.select(store, target_scope=None, num_inspirations=0)
        actual = restored_policy.select(
            restored_store, target_scope=None, num_inspirations=0
        )
        self.assertEqual(actual.parent.id, expected.parent.id)

    def test_malformed_checkpoint_state_is_rejected(self):
        selector = policy()
        state = selector.state_dict()
        state["visits"] = {"seed": 0}
        state["qualities"] = {"seed": 1.0}
        with self.assertRaises(ValueError):
            selector.load_state_dict(state)


class TestUCTComposition(unittest.TestCase):
    def test_tree_default_and_explicit_uct_construct(self):
        for policy_name in ("substrate_default", "uct"):
            config = NoemaConfig(
                substrate=SubstrateConfig(kind="tree", steps_per_generation=3),
                selection=SelectionConfig(policy=policy_name),
            )
            runtime = build_substrate_runtime(config)
            self.assertIsInstance(runtime.store, TreeStore)
            self.assertIsInstance(runtime.policy, UCTSelectionPolicy)
            self.assertEqual(runtime.steps_per_generation, 3)

    def test_uct_with_islands_fails_at_capability_boundary(self):
        config = NoemaConfig(
            substrate=SubstrateConfig(kind="islands"),
            selection=SelectionConfig(policy="uct"),
        )
        with self.assertRaisesRegex(ValueError, "tree_topology"):
            build_substrate_runtime(config)

    def test_legacy_islands_default_is_unchanged(self):
        runtime = build_substrate_runtime(NoemaConfig())
        self.assertIsInstance(runtime.store, IslandsStore)
        self.assertEqual(type(runtime.policy).__name__, "StockOpenEvolveSelection")

    def test_config_rejects_invalid_uct_parameters(self):
        for selection in (
            SelectionConfig(initial_exploration=-0.1),
            SelectionConfig(initial_exploration=float("inf")),
            SelectionConfig(widening_alpha=0),
            SelectionConfig(widening_alpha=1.1),
        ):
            with self.assertRaises(ValueError):
                NoemaConfig(selection=selection)

    def test_policy_imports_no_concrete_store(self):
        import noema.selection.uct as module

        source = inspect.getsource(module)
        self.assertNotIn("noema.tree", source)
        self.assertNotIn("from noema.tree", source)
        self.assertNotIn("import noema.tree", source)


class TestCrossSubstratePromptIdentity(unittest.TestCase):
    def test_same_parent_produces_identical_prompt_on_islands_and_tree(self):
        islands = IslandsStore(
            DatabaseConfig(
                in_memory=True,
                num_islands=1,
                population_size=10,
                migration_interval=1000,
            )
        )
        tree = TreeStore()
        islands.add(program("seed", 1.0), target_scope=0)
        tree.add(program("seed", 1.0))
        sampler = make_prompt_sampler(
            PromptConfig(use_template_stochasticity=False)
        )

        def prompt_for(store):
            parent = store.population()[0]
            return build_mutation_prompt(
                sampler,
                parent=parent,
                top_programs=[],
                previous_programs=[],
                inspirations=[],
                language="python",
                iteration=2,
                diff_based_evolution=False,
                feature_dimensions=[],
            )

        self.assertEqual(prompt_for(islands), prompt_for(tree))


if __name__ == "__main__":
    unittest.main()
