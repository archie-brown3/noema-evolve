"""Storage and topology contract for TreeStore (task 0083)."""

from __future__ import annotations

import copy
import inspect
import json
import tempfile
import unittest

from openevolve.database import Program

from noema.base import PopulationStore
from noema.tree import TreeStore


def program(program_id: str, score: float, parent_id=None, **extra) -> Program:
    return Program(
        id=program_id,
        code=f"def {program_id.replace('-', '_')}():\n    return {score}\n",
        language="python",
        parent_id=parent_id,
        metrics={"combined_score": score},
        **extra,
    )


class TestTreeStore(unittest.TestCase):
    def make_tree(self, **kwargs) -> TreeStore:
        return TreeStore(steps_per_generation=4, **kwargs)

    def seed_branches(self, store: TreeStore) -> None:
        store.add(program("seed", 1.0), iteration=1)
        store.add(program("alpha", 2.0, "seed"), iteration=2)
        store.add(program("beta", 3.0, "seed"), iteration=3)
        store.add(program("alpha-child", 4.0, "alpha"), iteration=4)

    def test_virtual_root_is_invisible_and_tree_is_global(self):
        store = self.make_tree()
        snapshot = store.snapshot()

        self.assertIsInstance(store, PopulationStore)
        self.assertEqual(store.population(), ())
        self.assertEqual(snapshot.top_programs, ())
        self.assertIsNone(snapshot.best_program)
        self.assertEqual(snapshot.regions, ())
        self.assertEqual(store.topology, "tree_branches")
        self.assertIsNone(store.target_scope(99))
        self.assertFalse(store.end_generation())
        self.assertFalse(hasattr(store, "select"))

    def test_trunk_branches_descendants_and_metadata_are_stable(self):
        store = self.make_tree()
        seed = program("seed", 1.0, metadata={"unchanged": True})
        store.add(seed)
        self.assertEqual(
            [(region.scope, region.label, region.size) for region in store.regions()],
            [("trunk:seed", "trunk", 1)],
        )

        store.add(program("alpha", 2.0, "seed"))
        store.add(program("beta", 3.0, "seed"))
        store.add(program("alpha-child", 4.0, "alpha"))

        self.assertEqual(seed.metadata, {"unchanged": True})
        self.assertEqual(
            [region.scope for region in store.regions()],
            ["trunk:seed", "branch:alpha", "branch:beta"],
        )
        self.assertEqual(
            [item.id for item in store.population("branch:alpha")],
            ["alpha", "alpha-child"],
        )
        self.assertEqual(
            [item.id for item in store.population("branch:beta")], ["beta"]
        )
        self.assertEqual([region.size for region in store.regions()], [1, 2, 1])
        self.assertEqual(store.num_programs, 4)

    def test_invalid_lineage_is_rejected_without_partial_mutation(self):
        store = self.make_tree()
        with self.assertRaisesRegex(ValueError, "parentless seed"):
            store.add(program("orphan", 1.0, "missing"))
        self.assertEqual(store.num_programs, 0)

        store.add(program("seed", 1.0))
        invalid = (
            program("second-root", 1.0),
            program("self", 1.0, "self"),
            program("missing", 1.0, "absent"),
            program("seed", 9.0, "seed"),
        )
        for item in invalid:
            with self.assertRaises(ValueError):
                store.add(item)
        with self.assertRaises(TypeError):
            store.add({"id": "dict"})
        self.assertEqual([item.id for item in store.population()], ["seed"])

    def test_working_set_prunes_context_without_deleting_programs(self):
        store = self.make_tree(working_set_size=2)
        store.add(program("seed", 1.0))
        store.add(program("zeta", 5.0, "seed"))
        store.add(program("alpha", 5.0, "seed"))
        store.add(program("middle", 4.0, "seed"))
        store.add(program("low", 2.0, "seed"))

        self.assertEqual(
            [item.id for item in store.working_programs()], ["alpha", "middle"]
        )
        self.assertEqual(
            [item.id for item in store.top_programs(5)], ["alpha", "middle"]
        )
        self.assertEqual([item.id for item in store.elites()], ["alpha", "middle"])
        self.assertEqual(store.best_program().id, "alpha")
        self.assertEqual(store.num_programs, 5)
        self.assertEqual(
            [item.id for item in store.population("branch:zeta")], ["zeta"]
        )
        self.assertEqual(len(store.snapshot().fitnesses), 5)

    def test_snapshots_are_views_with_deterministic_regions_and_ties(self):
        store = self.make_tree(working_set_size=3)
        self.seed_branches(store)
        snapshot = store.snapshot(limit=2)

        self.assertEqual(snapshot.topology, "tree_branches")
        self.assertEqual(
            [view.id for view in snapshot.top_programs], ["alpha-child", "beta"]
        )
        self.assertEqual(snapshot.best_program.id, "alpha-child")
        self.assertEqual(snapshot.fitnesses, (2.0, 4.0, 3.0, 1.0))
        self.assertEqual(
            [(region.scope, region.best_fitness, region.size) for region in snapshot.regions],
            [
                ("trunk:seed", 1.0, 1),
                ("branch:alpha", 4.0, 2),
                ("branch:beta", 3.0, 1),
            ],
        )
        snapshot.top_programs[0].metrics["combined_score"] = -1
        self.assertEqual(store.fitness(store.best_program()), 4.0)

        local = store.snapshot("branch:alpha")
        self.assertEqual([view.id for view in local.top_programs], ["alpha-child", "alpha"])
        self.assertEqual(local.regions, ())

    def test_json_and_file_round_trip_preserve_complete_state(self):
        store = self.make_tree(
            working_set_size=3, feature_dimensions=("complexity",)
        )
        store.add(
            program("seed", 1.0, changes_description="seed", generation=1),
            iteration=1,
        )
        store.add(
            program("alpha", 3.0, "seed", prompts={"user": "hello"}),
            iteration=2,
        )
        store.add(program("beta", 2.0, "seed"), iteration=3)
        store.store_artifacts("alpha", {"note": "kept", "payload": b"bytes"})
        state = json.loads(json.dumps(store.state_dict()))

        restored = self.make_tree()
        restored.load_state_dict(state)
        self.assertEqual(restored.state_dict(), state)
        self.assertEqual(
            [item.id for item in restored.working_programs()],
            ["alpha", "beta", "seed"],
        )
        self.assertEqual(restored._artifacts["alpha"]["payload"], b"bytes")
        self.assertEqual(restored.last_iteration, 3)
        self.assertEqual(restored.regions(), store.regions())
        self.assertEqual(restored.best_program().id, store.best_program().id)

        with tempfile.TemporaryDirectory() as directory:
            store.save(directory, iteration=9)
            loaded = self.make_tree()
            loaded.load(directory)
            self.assertEqual(loaded.last_iteration, 9)
            self.assertEqual(loaded.state_dict(), store.state_dict())

    def test_corrupt_state_is_rejected_without_replacing_live_state(self):
        source = self.make_tree()
        self.seed_branches(source)
        state = json.loads(json.dumps(source.state_dict()))

        target = self.make_tree()
        target.add(program("existing", 7.0))
        before = target.state_dict()

        corrupt_states = []
        wrong_working_set = copy.deepcopy(state)
        wrong_working_set["working_set_ids"] = []
        corrupt_states.append(wrong_working_set)

        mismatched_children = copy.deepcopy(state)
        mismatched_children["children"]["seed"] = []
        corrupt_states.append(mismatched_children)

        wrong_parent = copy.deepcopy(state)
        wrong_parent["programs"]["alpha"]["parent_id"] = "beta"
        corrupt_states.append(wrong_parent)

        disconnected_cycle = copy.deepcopy(state)
        disconnected_cycle["parents"]["alpha"] = "alpha-child"
        disconnected_cycle["programs"]["alpha"]["parent_id"] = "alpha-child"
        disconnected_cycle["children"]["seed"] = ["beta"]
        disconnected_cycle["children"]["alpha-child"] = ["alpha"]
        corrupt_states.append(disconnected_cycle)

        for corrupt in corrupt_states:
            with self.assertRaises(ValueError):
                target.load_state_dict(corrupt)
            self.assertEqual(target.state_dict(), before)

    def test_selection_boundary_and_source_imports_are_explicit(self):
        store = self.make_tree()
        store.add(program("seed", 1.0))
        with self.assertRaisesRegex(RuntimeError, "no native selection"):
            store.native_select(None, 0)
        source = inspect.getsource(TreeStore)
        self.assertNotIn("noema.selection", source)
        self.assertNotIn("UCT", source)


if __name__ == "__main__":
    unittest.main()
