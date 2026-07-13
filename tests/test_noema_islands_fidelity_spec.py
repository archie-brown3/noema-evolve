"""Executable fidelity specification for the future islands store.

These tests deliberately fail until ``noema.substrate.islands`` exists.  Imports
of that future module stay inside the test fixture so unittest can discover and
report each missing contract without failing while this module itself imports.

The compatibility surface specified here is intentionally small::

    IslandsStore(DatabaseConfig(...))
    store.add(program, iteration=None, target_scope=None)
    store.native_select(target_scope, num_inspirations)

``stock`` means OpenEvolve's behavior, not a reimplementation of it.  In
particular, preserving only the distribution is insufficient: seeded traces and
the process-global ``random`` state must be identical to a direct
``ProgramDatabase.sample_from_island`` call.
"""

import copy
import random
import unittest
from unittest import mock

from openevolve.config import DatabaseConfig
from openevolve.database import Program, ProgramDatabase


def _config(**overrides):
    values = dict(
        in_memory=True,
        log_prompts=False,
        num_islands=3,
        population_size=50,
        archive_size=20,
        feature_dimensions=["complexity", "diversity"],
        exploration_ratio=0.25,
        exploitation_ratio=0.50,
        random_seed=None,
        migration_interval=100,
    )
    values.update(overrides)
    return DatabaseConfig(**values)


def _program(program_id, island, score):
    return Program(
        id=program_id,
        code=f"def {program_id}():\n    return {score}\n",
        language="python",
        metrics={"combined_score": score},
        metadata={"island": island, "fixture": {"stable": True}},
    )


def _populate_without_randomness(database):
    """Install a deterministic population without consuming either RNG."""
    layout = (
        ("a0", 0, 0.10),
        ("a1", 0, 0.35),
        ("a2", 0, 0.80),
        ("c0", 2, 0.20),
        ("c1", 2, 0.65),
        ("c2", 2, 0.95),
    )
    programs = [_program(*row) for row in layout]
    database.programs = {program.id: program for program in programs}
    database.islands = [
        {"a0", "a1", "a2"},
        set(),
        {"c0", "c1", "c2"},
    ]
    database.archive = {"a2", "c2"}
    database.island_best_programs = ["a2", None, "c2"]
    database.best_program_id = "c2"


def _ids(selection):
    if isinstance(selection, tuple):
        parent, inspirations = selection
        return parent.id, tuple(program.id for program in inspirations)
    return (
        selection.parent.id,
        tuple(program.id for program in selection.inspirations),
    )


def _numpy_state_equal(left, right):
    # RandomState state is (algorithm, uint32 array, position, gaussian flag,
    # cached gaussian).  array_equal avoids NumPy's ambiguous tuple equality.
    import numpy as np

    return (
        left[0] == right[0]
        and np.array_equal(left[1], right[1])
        and left[2:] == right[2:]
    )


class IslandsStockFidelitySpec(unittest.TestCase):
    def setUp(self):
        self._python_random_state = random.getstate()
        self.addCleanup(random.setstate, self._python_random_state)

    def _store_class(self):
        # Lazy by design: the future module must not break test discovery before
        # its implementation lands.
        try:
            from noema.substrate.islands import IslandsStore
        except ImportError as exc:
            self.fail(f"missing planned IslandsStore module: {exc}")
        return IslandsStore

    def _store(self, config=None):
        IslandsStore = self._store_class()
        store = IslandsStore(config or _config())
        _populate_without_randomness(store._db)
        return store

    def _direct(self, config=None):
        database = ProgramDatabase(config or _config())
        _populate_without_randomness(database)
        return database

    def test_stock_is_one_atomic_delegation_to_openevolve(self):
        store = self._store()
        delegated_result = (
            store._db.programs["a1"],
            [store._db.programs["a0"], store._db.programs["a2"]],
        )

        random.seed(9173)
        with mock.patch.object(
            store._db,
            "sample_from_island",
            return_value=delegated_result,
        ) as delegated:
            actual = store.native_select(0, num_inspirations=2)

        delegated.assert_called_once_with(0, num_inspirations=2)
        self.assertIs(actual.parent, delegated_result[0])
        self.assertIs(actual.inspirations[0], delegated_result[1][0])
        self.assertEqual(actual.source_scope, 0)
        self.assertEqual(actual.target_scope, 0)

    def test_stock_propagates_delegate_failure_without_retry(self):
        store = self._store()
        failure = RuntimeError("single atomic sampling attempt")
        with mock.patch.object(
            store._db, "sample_from_island", side_effect=failure
        ) as delegated:
            with self.assertRaisesRegex(RuntimeError, "single atomic sampling attempt"):
                store.native_select(2, num_inspirations=3)

        delegated.assert_called_once_with(2, num_inspirations=3)

    def test_stock_seeded_traces_and_python_rng_state_equal_direct_openevolve(self):
        store = self._store()
        direct = self._direct()
        trace = ((11, 0, 2), (29, 2, 1), (47, 0, 5), (83, 2, 0))

        for seed, source_island, inspiration_count in trace:
            with self.subTest(seed=seed, source_island=source_island):
                random.seed(seed)
                expected = direct.sample_from_island(
                    source_island, num_inspirations=inspiration_count
                )
                expected_state = random.getstate()

                random.seed(seed)
                actual = store.native_select(
                    source_island, num_inspirations=inspiration_count
                )
                actual_state = random.getstate()

                self.assertEqual(_ids(actual), _ids(expected))
                self.assertEqual(actual_state, expected_state)

    def test_interface_runtime_and_snapshots_do_not_perturb_stock_rng_trace(self):
        from noema.substrate.base import SubstrateRuntime
        from noema.substrate.selection.stock_openevolve import (
            StockOpenEvolveSelection,
        )

        store = self._store()
        runtime = SubstrateRuntime(store, StockOpenEvolveSelection())
        direct = self._direct()

        random.seed(20260713)
        expected = direct.sample_from_island(2, num_inspirations=3)
        expected_state = random.getstate()

        random.seed(20260713)
        store.snapshot(2, limit=5)
        store.snapshot(None, limit=5)
        actual = runtime.select(target_scope=2, num_inspirations=3)
        actual_state = random.getstate()

        self.assertEqual(_ids(actual), _ids(expected))
        self.assertEqual(actual_state, expected_state)

    def test_empty_source_island_has_openevolve_global_fallback(self):
        store = self._store()
        direct = self._direct()

        # Island 1 is empty.  OpenEvolve falls through to global sample(), whose
        # current island is 0 in this fixture.  Pin both the result and every
        # consumed bit of Python RNG state.
        for seed in (3, 101, 809):
            with self.subTest(seed=seed):
                random.seed(seed)
                expected = direct.sample_from_island(1, num_inspirations=2)
                expected_state = random.getstate()

                random.seed(seed)
                actual = store.native_select(1, num_inspirations=2)
                actual_state = random.getstate()

                self.assertEqual(_ids(actual), _ids(expected))
                self.assertEqual(actual_state, expected_state)
                self.assertNotEqual(actual.parent.metadata["island"], 1)
                self.assertEqual(actual.source_scope, actual.parent.metadata["island"])
                self.assertEqual(actual.target_scope, 1)

    def test_stock_has_no_numpy_or_program_metadata_side_effects(self):
        import numpy as np

        store = self._store()
        original_numpy_state = np.random.get_state()
        self.addCleanup(np.random.set_state, original_numpy_state)
        np.random.seed(271828)
        numpy_before = copy.deepcopy(np.random.get_state())
        metadata_before = {
            pid: copy.deepcopy(program.metadata)
            for pid, program in store._db.programs.items()
        }

        random.seed(314159)
        store.native_select(2, num_inspirations=2)

        numpy_after = np.random.get_state()
        metadata_after = {
            pid: program.metadata for pid, program in store._db.programs.items()
        }
        self.assertTrue(_numpy_state_equal(numpy_before, numpy_after))
        self.assertEqual(metadata_after, metadata_before)

    def test_write_target_and_selection_source_are_distinct_scopes(self):
        store = self._store()
        child = _program("targeted", 99, 0.55)

        # A targeted write must neither retarget the next source sample nor rely
        # on current_island as an implicit communication channel.
        original_current_island = store._db.current_island
        store.add(child, iteration=7, target_scope=1)
        self.assertIn("targeted", store._db.islands[1])
        self.assertEqual(child.metadata["island"], 1)
        self.assertEqual(store._db.current_island, original_current_island)

        with mock.patch.object(
            store._db,
            "sample_from_island",
            wraps=store._db.sample_from_island,
        ) as delegated:
            random.seed(1234)
            selection = store.native_select(2, num_inspirations=1)

        delegated.assert_called_once_with(2, num_inspirations=1)
        self.assertEqual(selection.parent.metadata["island"], 2)
        self.assertEqual(selection.source_scope, 2)
        self.assertEqual(selection.target_scope, 2)

    def test_omitted_selection_and_old_database_config_mean_stock(self):
        from noema.config import NoemaConfig
        from noema.substrate.base import SubstrateRuntime
        from noema.substrate.registry import build_substrate_runtime
        from noema.substrate.selection.stock_openevolve import StockOpenEvolveSelection

        IslandsStore = self._store_class()

        # This is the pre-IslandsStore database shape: it contains no selection
        # key.  Loading it must continue to select stock behavior.
        legacy_values = dict(
            in_memory=True,
            log_prompts=False,
            num_islands=3,
            population_size=50,
            archive_size=20,
            feature_dimensions=["complexity", "diversity"],
            exploration_ratio=0.25,
            exploitation_ratio=0.50,
            random_seed=None,
        )
        old_config = NoemaConfig.from_dict({"database": legacy_values})
        default_runtime = build_substrate_runtime(old_config)
        explicit_runtime = SubstrateRuntime(
            IslandsStore(DatabaseConfig(**legacy_values)), StockOpenEvolveSelection()
        )
        _populate_without_randomness(default_runtime.store._db)
        _populate_without_randomness(explicit_runtime.store._db)

        for seed, source_island in ((7, 0), (13, 1), (19, 2)):
            with self.subTest(seed=seed, source_island=source_island):
                random.seed(seed)
                default_result = default_runtime.select(
                    target_scope=source_island, num_inspirations=2
                )
                default_state = random.getstate()

                random.seed(seed)
                explicit_result = explicit_runtime.select(
                    target_scope=source_island, num_inspirations=2
                )
                explicit_state = random.getstate()

                self.assertEqual(_ids(default_result), _ids(explicit_result))
                self.assertEqual(default_state, explicit_state)


if __name__ == "__main__":
    unittest.main()
