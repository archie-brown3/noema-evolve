"""Behavioural contract for HiFo's probability-rank parent selection."""

from __future__ import annotations

import random
import json
import unittest

from openevolve.database import Program
from openevolve.config import DatabaseConfig

from noema.config import NoemaConfig, SelectionConfig, SubstrateConfig
from noema.selection.hifo_prob_rank import HiFoProbRankSelection
from noema.substrates.flat import FlatPopulationStore
from noema.substrates.registry import build_substrate_runtime


def program(program_id: str, score: float) -> Program:
    return Program(
        id=program_id,
        code=f"def {program_id}():\n    return {score}\n",
        language="python",
        metrics={"combined_score": score},
    )


class TestHiFoProbRankSelection(unittest.TestCase):
    def test_draws_one_parent_from_source_rank_weights(self):
        store = FlatPopulationStore(population_size=3)
        store.add(program("low", 0.3))
        store.add(program("middle", 0.7))
        store.add(program("high", 0.9))
        policy = HiFoProbRankSelection(random_seed=17)

        selected = policy.select(store)

        expected = random.Random(17).choices(
            ["high", "middle", "low"],
            weights=[1 / 4, 1 / 5, 1 / 6],
            k=1,
        )[0]
        self.assertEqual(selected.parent.id, expected)
        self.assertEqual(selected.inspirations, ())
        self.assertIsNone(selected.source_scope)
        self.assertIsNone(selected.target_scope)

    def test_json_checkpoint_continues_the_seeded_draw_sequence(self):
        store = FlatPopulationStore(population_size=3)
        store.add(program("low", 0.3))
        store.add(program("middle", 0.7))
        store.add(program("high", 0.9))
        policy = HiFoProbRankSelection(random_seed=17)
        policy.select(store)
        state = json.loads(json.dumps(policy.state_dict()))

        restored = HiFoProbRankSelection(random_seed=99)
        restored.load_state_dict(state)

        self.assertEqual(restored.select(store).parent.id, policy.select(store).parent.id)

    def test_empty_population_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty flat population"):
            HiFoProbRankSelection(random_seed=1).select(FlatPopulationStore(population_size=3))

    def test_flat_substrate_defaults_to_hifo_probability_rank_selection(self):
        runtime = build_substrate_runtime(
            NoemaConfig(
                database=DatabaseConfig(in_memory=True, population_size=3),
                substrate=SubstrateConfig(kind="flat"),
                selection=SelectionConfig(policy="substrate_default", seed=17),
            )
        )

        self.assertIsInstance(runtime.store, FlatPopulationStore)
        self.assertIsInstance(runtime.policy, HiFoProbRankSelection)
