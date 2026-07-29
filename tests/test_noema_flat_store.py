"""Behavioural contract for the bounded, global HiFo-compatible population."""

from __future__ import annotations

import unittest
import json
import tempfile

from openevolve.database import Program

from noema.substrates.base import PopulationStore
from noema.substrates.flat import FlatPopulationStore


def program(program_id: str, score: float) -> Program:
    return Program(
        id=program_id,
        code=f"def {program_id}():\n    return {score}\n",
        language="python",
        metrics={"combined_score": score},
    )


class TestFlatPopulationStore(unittest.TestCase):
    def test_retains_the_two_best_distinct_objectives_in_one_global_scope(self):
        store = FlatPopulationStore(population_size=2)

        store.add(program("first_tie", 0.7))
        store.add(program("second_tie", 0.7))
        store.add(program("low", 0.1))
        store.add(program("high", 0.9))

        self.assertIsNone(store.target_scope(99))
        self.assertEqual(
            [candidate.id for candidate in store.population()],
            ["high", "first_tie"],
        )
        self.assertEqual(store.num_programs, 2)

    def test_discards_non_survivors_and_their_artifacts(self):
        store = FlatPopulationStore(population_size=1)
        self.assertEqual(store.add(program("winner", 0.9)), "winner")
        store.store_artifacts("winner", {"note": "kept"})

        self.assertIsNone(store.add(program("loser", 0.1)))
        self.assertEqual([candidate.id for candidate in store.population()], ["winner"])
        self.assertEqual(store._artifacts, {"winner": {"note": "kept"}})
        with self.assertRaisesRegex(ValueError, "unknown program"):
            store.store_artifacts("loser", {"note": "discarded"})

    def test_round_trips_the_retained_population_and_artifacts_as_json(self):
        store = FlatPopulationStore(
            population_size=2,
            steps_per_generation=3,
            feature_dimensions=("combined_score",),
        )
        store.add(program("alpha", 0.9), iteration=4)
        store.add(program("beta", 0.7), iteration=5)
        store.store_artifacts("alpha", {"payload": b"bytes"})

        state = json.loads(json.dumps(store.state_dict()))
        restored = FlatPopulationStore(population_size=1)
        restored.load_state_dict(state)

        self.assertEqual(restored.state_dict(), state)
        self.assertEqual([candidate.id for candidate in restored.population()], ["alpha", "beta"])
        self.assertEqual(restored._artifacts["alpha"]["payload"], b"bytes")
        self.assertEqual(restored.last_iteration, 5)

    def test_save_records_its_checkpoint_iteration_and_loads_it(self):
        store = FlatPopulationStore(population_size=1)
        store.add(program("alpha", 0.9), iteration=2)

        with tempfile.TemporaryDirectory() as checkpoint_dir:
            store.save(checkpoint_dir, iteration=7)
            restored = FlatPopulationStore(population_size=2)
            restored.load(checkpoint_dir)

        self.assertEqual(restored.last_iteration, 7)
        self.assertEqual([candidate.id for candidate in restored.population()], ["alpha"])

    def test_exposes_the_neutral_population_store_contract(self):
        store = FlatPopulationStore(population_size=3)
        store.add(program("middle", 0.7))
        store.add(program("high", 0.9))
        store.add(program("low", 0.3))

        self.assertIsInstance(store, PopulationStore)
        self.assertEqual([candidate.id for candidate in store.elites()], ["high", "middle", "low"])
        self.assertEqual(store.best_program().id, "high")
        self.assertEqual(store.all_fitnesses(), (0.9, 0.7, 0.3))
        self.assertEqual(store.regions(), ())
        self.assertEqual(store.per_scope_bests(), ())
        self.assertFalse(store.end_generation())
        with self.assertRaisesRegex(RuntimeError, "no native selection"):
            store.native_select(None, 0)
