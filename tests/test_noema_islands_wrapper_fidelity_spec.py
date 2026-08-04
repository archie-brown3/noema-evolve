"""Islands fidelity specs exercised THROUGH Noema's wrapper public API (task 0188).

Why this file exists
    ``tests/test_openevolve_pin_regression_islands.py`` runs OpenEvolve's donor
    island tests against the pinned dependency. Those prove nothing about Noema:
    sabotaging every ``IslandsStore`` method leaves them green. This file
    translates the same donor behaviors to run through ``IslandsStore``'s public
    surface, so it fails if Noema's wrapper breaks.

Relationship to ``tests/test_noema_islands_fidelity_spec.py``
    Sibling, not replacement. That file pins ``native_select`` delegation, seeded
    RNG-trace equality against a direct ``ProgramDatabase``, empty-island global
    fallback, and numpy/metadata non-perturbation -- all still valuable. Its one
    weakness is that it seeds state by writing ``_db.programs``/``islands``/
    ``archive`` directly, so nothing there proves ``add()`` works. This file only
    ever writes through ``store.add(...)``.

Hard rules for anything added here
    1. Seed populations ONLY via ``store.add(...)``. Never touch ``_db`` to write.
    2. Island-best VALUE assertions use ``store.per_scope_bests()[i]``, never
       ``top_programs(1, scope=i)[0].id``. ``get_top_programs`` ranks by metric
       average; ``per_scope_bests`` routes through ``store.fitness`` ==
       ``get_fitness_score``, the same scalar upstream's island_best uses.
    3. Drive migration with repeated ``store.end_generation()``, never by
       assigning ``island_generations`` and calling ``migrate_programs()``.

MAP-Elites eviction is real and load-bearing
    ``add()`` places a program in a feature cell; a later program mapping to the
    same cell evicts the weaker one from the island set (it survives in
    ``_db.programs``). Feature bins are scaled against running min/max stats, so
    which programs coexist is not predictable from the fixture alone. Tests here
    therefore assert on the SURVIVOR set returned by ``_seed(...)`` rather than
    assuming every added program stays. Note eviction is monotone -- a cell only
    ever upgrades -- so ``per_scope_bests()`` claims are stable regardless.

Pinned-as-observed inconsistency (not endorsed)
    ``population()``, ``native_select()`` and ``island_fitnesses()`` apply
    ``% num_islands``; ``top_programs()`` does not and propagates upstream's
    ``IndexError``. Donor tests pin both sides, so both are translated here. This
    asymmetry is recorded as current behavior, not as an intended contract.
"""

from __future__ import annotations

import random
import unittest
from typing import Dict, Iterable, Optional, Sequence, Set, Tuple

from openevolve.config import DatabaseConfig
from openevolve.database import Program

from noema.substrates.islands import IslandsStore


def _config(**overrides) -> DatabaseConfig:
    values = dict(
        in_memory=True,
        log_prompts=False,
        num_islands=3,
        population_size=100,
        archive_size=20,
        migration_interval=1000,  # keep migration out unless a test asks for it
        random_seed=42,
    )
    values.update(overrides)
    return DatabaseConfig(**values)


def _store(**overrides) -> IslandsStore:
    return IslandsStore(_config(**overrides))


def _program(
    program_id: str,
    score: float,
    *,
    parent_id: Optional[str] = None,
    code: Optional[str] = None,
    metrics: Optional[Dict[str, float]] = None,
    metadata: Optional[Dict[str, object]] = None,
) -> Program:
    return Program(
        id=program_id,
        code=code if code is not None else f"def {program_id}():\n    return {score}\n",
        language="python",
        parent_id=parent_id,
        metrics=metrics if metrics is not None else {"combined_score": score},
        metadata=dict(metadata) if metadata else {},
    )


def _seed(
    store: IslandsStore,
    scope: Optional[int],
    specs: Iterable[Tuple[str, float]],
    *,
    start_iteration: int = 0,
) -> Set[str]:
    """Add programs through the wrapper; return the ids that SURVIVED on `scope`.

    MAP-Elites may evict a program from the island set on insert, so the caller
    must assert against the survivor set rather than the requested set.
    """
    for offset, (program_id, score) in enumerate(specs):
        store.add(
            _program(program_id, score),
            iteration=start_iteration + offset,
            target_scope=scope,
        )
    return _ids(store.population(scope))


def _ids(programs: Sequence[Program]) -> Set[str]:
    return {program.id for program in programs}


def _run_to_migration(store: IslandsStore, max_sweeps: int = 200) -> int:
    """Call end_generation() until it reports a migration. Returns sweeps used."""
    for sweep in range(1, max_sweeps + 1):
        if store.end_generation():
            return sweep
    raise AssertionError(f"no migration within {max_sweeps} sweeps")


class _IslandsWrapperTestCase(unittest.TestCase):
    """Restores global RNG state; SubstrateDatabase seeds it at construction."""

    def setUp(self) -> None:
        state = random.getstate()
        self.addCleanup(random.setstate, state)


class TestIslandGenerationBookkeeping(_IslandsWrapperTestCase):
    """Guards the end_generation() per-island repair.

    OpenEvolve's controller advances the generation counter per iteration with an
    explicit island (process_parallel.py:618,
    ``increment_island_generation(island_idx=island_id)``). Noema drives one
    ``end_generation()`` per full round-robin sweep, so it must advance every
    island. Calling it bare defaults to ``_db.current_island``, which Noema never
    moves off 0 -- that produced ``[G, 0, 0]`` where upstream produces ``[G,G,G]``.
    """

    @staticmethod
    def _upstream_controller_sweeps(config: DatabaseConfig, sweeps: int):
        """Replay upstream's own per-iteration bookkeeping pattern."""
        reference = IslandsStore(config)._db
        fired = []
        for sweep in range(sweeps):
            for island in range(len(reference.islands)):
                reference.increment_island_generation(island_idx=island)
            if reference.should_migrate():
                reference.migrate_programs()
                fired.append(sweep)
        return reference, fired

    def test_end_generation_advances_every_island_not_just_island_zero(self):
        store = _store(num_islands=3)

        for _ in range(12):
            store.end_generation()

        self.assertEqual(store._db.island_generations, [12, 12, 12])

    def test_generation_vector_matches_upstream_controller_pattern(self):
        sweeps = 12
        store = _store(num_islands=3, migration_interval=5)
        for _ in range(sweeps):
            store.end_generation()

        reference, _ = self._upstream_controller_sweeps(
            _config(num_islands=3, migration_interval=5), sweeps
        )

        self.assertEqual(store._db.island_generations, reference.island_generations)

    def test_migration_cadence_matches_upstream_controller_pattern(self):
        sweeps = 12
        store = _store(num_islands=3, migration_interval=5)
        fired = [sweep for sweep in range(sweeps) if store.end_generation()]

        _, reference_fired = self._upstream_controller_sweeps(
            _config(num_islands=3, migration_interval=5), sweeps
        )

        # This equality is what made the repair safe: only max(island_generations)
        # is behavioural, and the repair leaves max unchanged.
        self.assertEqual(fired, reference_fired)

    def test_end_generation_reports_false_until_the_interval_is_reached(self):
        store = _store(num_islands=3, migration_interval=5)

        results = [store.end_generation() for _ in range(6)]

        self.assertEqual(results, [False, False, False, False, True, False])


class TestIslandPlacementThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_island_child_placement.py 1/2/4/5/6/8,
    test_island_parent_consistency.py:132, test_island_migration.py:47."""

    def test_child_inherits_parent_island_when_no_target_specified(self):
        """donor: child_placement.py:24"""
        store = _store()
        store.add(_program("parent_0", 0.5), iteration=0, target_scope=0)

        child = _program("child_0", 0.6, parent_id="parent_0")
        store.add(child, iteration=1)

        self.assertEqual(child.metadata.get("island"), 0)
        self.assertIn("child_0", _ids(store.population(0)))

    def test_child_placed_in_target_island_when_specified(self):
        """donor: child_placement.py:49"""
        store = _store()
        store.add(_program("parent_1", 0.5), iteration=0, target_scope=0)

        child = _program("child_1", 0.6, parent_id="parent_1")
        store.add(child, iteration=1, target_scope=2)

        self.assertEqual(child.metadata.get("island"), 2)
        self.assertIn("child_1", _ids(store.population(2)))
        self.assertNotIn("child_1", _ids(store.population(0)))

    def test_child_goes_to_target_island_not_the_fallback_parents_island(self):
        """donor: child_placement.py:114 (issue #391) -- parent is sourced from a
        populated island, but the child must still land on the requested one."""
        store = _store()
        store.add(_program("seed_0", 0.5), iteration=0, target_scope=0)

        selection = store.native_select(1, num_inspirations=0)
        self.assertEqual(selection.source_scope, 0)
        self.assertEqual(selection.target_scope, 1)

        child = _program("child_t1", 0.6, parent_id=selection.parent.id)
        store.add(child, iteration=1, target_scope=selection.target_scope)

        self.assertIn("child_t1", _ids(store.population(1)))
        self.assertEqual(child.metadata.get("island"), 1)

    def test_explicit_target_island_overrides_parent_inheritance(self):
        """donor: child_placement.py:151"""
        store = _store()
        store.add(_program("seed_2", 0.5), iteration=0, target_scope=0)

        selection = store.native_select(2, num_inspirations=0)
        child = _program("child_t2", 0.6, parent_id=selection.parent.id)
        store.add(child, iteration=1, target_scope=2)

        self.assertIn("child_t2", _ids(store.population(2)))
        self.assertNotIn("child_t2", _ids(store.population(0)))

    def test_with_target_island_child_goes_to_target(self):
        """donor: child_placement.py:293 (near-duplicate of :151, kept for
        one-for-one donor traceability)."""
        store = _store()
        store.add(_program("seed_3", 0.5), iteration=0, target_scope=0)

        selection = store.native_select(2, num_inspirations=0)
        child = _program("child_t3", 0.7, parent_id=selection.parent.id)
        store.add(child, iteration=1, target_scope=2)

        self.assertEqual(child.metadata.get("island"), 2)
        self.assertIn("child_t3", _ids(store.population(2)))

    def test_round_robin_target_scope_populates_every_island(self):
        """donor: child_placement.py:189 -- driven by the wrapper's own
        target_scope() round-robin rather than a hand-written island counter."""
        store = _store()
        store.add(_program("origin", 0.5), iteration=0, target_scope=0)

        for iteration in range(1, 10):
            scope = store.target_scope(iteration)
            selection = store.native_select(scope, num_inspirations=0)
            child = _program(f"rr_{iteration}", 0.5 + iteration * 0.01,
                             parent_id=selection.parent.id)
            store.add(child, iteration=iteration, target_scope=scope)

        for region in store.regions():
            self.assertGreaterEqual(
                region.size, 1, f"{region.label} should have been populated"
            )

    def test_explicit_migration_target_overrides_parent_island(self):
        """donor: parent_consistency.py:132"""
        store = _store()
        store.add(_program("pc_parent", 0.5), iteration=0, target_scope=0)
        self.assertIn("pc_parent", _ids(store.population(0)))

        migrant = _program("pc_migrant", 0.7, parent_id="pc_parent")
        store.add(migrant, iteration=1, target_scope=2)

        self.assertIn("pc_migrant", _ids(store.population(2)))
        self.assertNotIn("pc_migrant", _ids(store.population(0)))
        self.assertEqual(migrant.metadata.get("island"), 2)
        self.assertEqual(store.get("pc_parent").metadata.get("island"), 0)

    def test_programs_are_assigned_to_their_requested_islands(self):
        """donor: migration.py:47"""
        store = _store()
        store.add(_program("t1", 0.5), iteration=0, target_scope=0)
        store.add(_program("t2", 0.7), iteration=1, target_scope=1)
        store.add(_program("t3", 0.3), iteration=2, target_scope=2)

        self.assertIn("t1", _ids(store.population(0)))
        self.assertIn("t2", _ids(store.population(1)))
        self.assertIn("t3", _ids(store.population(2)))
        for program_id, expected in (("t1", 0), ("t2", 1), ("t3", 2)):
            self.assertEqual(store.get(program_id).metadata.get("island"), expected)


class TestIslandBestTrackingThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_island_tracking.py 1/2/3/4/5/6/8/12.

    Island-best is asserted via per_scope_bests(), which routes through
    store.fitness == get_fitness_score -- the same scalar upstream's
    island_best_programs uses. top_programs ranks by metric average and would
    agree only by coincidence on these fixtures.
    """

    def test_every_island_starts_with_no_best(self):
        """donor: tracking.py:31"""
        store = _store()

        self.assertEqual(list(store.per_scope_bests()), [0.0, 0.0, 0.0])
        for scope in range(3):
            self.assertEqual(list(store.top_programs(1, scope=scope)), [])

    def test_first_program_added_becomes_that_islands_best(self):
        """donor: tracking.py:38"""
        store = _store()
        store.add(_program("first", 0.5), iteration=0, target_scope=0)

        self.assertEqual(store.per_scope_bests()[0], 0.5)
        self.assertEqual(store.per_scope_bests()[1], 0.0)
        self.assertEqual(store.per_scope_bests()[2], 0.0)

    def test_better_program_raises_the_island_best(self):
        """donor: tracking.py:50"""
        store = _store()
        store.add(_program("mediocre", 0.5), iteration=0, target_scope=0)
        self.assertEqual(store.per_scope_bests()[0], 0.5)

        store.add(_program("better", 0.8), iteration=1, target_scope=0)

        self.assertEqual(store.per_scope_bests()[0], 0.8)

    def test_worse_program_does_not_lower_the_island_best(self):
        """donor: tracking.py:62"""
        store = _store()
        store.add(_program("good", 0.8), iteration=0, target_scope=0)

        store.add(_program("worse", 0.3), iteration=1, target_scope=0)

        self.assertEqual(store.per_scope_bests()[0], 0.8)

    def test_island_bests_are_tracked_independently(self):
        """donor: tracking.py:76"""
        store = _store()
        store.add(_program("i0", 0.9), iteration=0, target_scope=0)
        store.add(_program("i1", 0.7), iteration=1, target_scope=1)
        store.add(_program("i2", 0.5), iteration=2, target_scope=2)

        self.assertEqual(list(store.per_scope_bests()), [0.9, 0.7, 0.5])
        self.assertEqual(
            [region.best_fitness for region in store.regions()], [0.9, 0.7, 0.5]
        )

    def test_program_added_to_an_empty_island_becomes_its_best(self):
        """donor: tracking.py:92 -- upstream builds the migrant by hand, so this
        is a targeted add rather than a real migration."""
        store = _store()
        store.add(_program("original", 0.6), iteration=0, target_scope=0)
        self.assertEqual(store.per_scope_bests()[1], 0.0)

        migrant = _program(
            "original_migrant_1", 0.6,
            parent_id="original", metadata={"migrant": True},
        )
        store.add(migrant, iteration=1, target_scope=1)

        self.assertEqual(store.per_scope_bests()[1], 0.6)
        self.assertIn("original_migrant_1", _ids(store.population(1)))

    def test_combined_score_not_raw_score_drives_the_island_best(self):
        """donor: tracking.py:147"""
        store = _store()
        store.add(
            _program("cs1", 0.0, metrics={"score": 0.5, "other": 0.3,
                                          "combined_score": 0.4}),
            iteration=0, target_scope=0,
        )
        self.assertEqual(store.per_scope_bests()[0], 0.4)

        # Lower `score`, higher `combined_score` -- combined_score must win.
        store.add(
            _program("cs2", 0.0, metrics={"score": 0.3, "other": 0.7,
                                          "combined_score": 0.5}),
            iteration=1, target_scope=0,
        )

        self.assertEqual(store.per_scope_bests()[0], 0.5)

    def test_island_bests_persist_across_later_weaker_additions(self):
        """donor: tracking.py:230"""
        store = _store()
        store.add(_program("best0", 0.9), iteration=0, target_scope=0)
        store.add(_program("best1", 0.8), iteration=1, target_scope=1)
        self.assertEqual(list(store.per_scope_bests())[:2], [0.9, 0.8])

        store.add(_program("worse0", 0.5), iteration=2, target_scope=0)
        store.add(_program("worse1", 0.4), iteration=3, target_scope=1)

        self.assertEqual(list(store.per_scope_bests())[:2], [0.9, 0.8])


class TestIslandTopProgramsThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_island_tracking.py 7/13/14."""

    def test_top_programs_are_scoped_to_the_requested_island(self):
        """donor: tracking.py:118"""
        store = _store()
        island0 = _seed(store, 0, [("prog1", 0.9), ("prog2", 0.7), ("prog3", 0.5)])
        island1 = _seed(store, 1, [("prog4", 0.8), ("prog5", 0.6)],
                        start_iteration=3)

        for program in store.top_programs(2, scope=0):
            self.assertIn(program.id, island0)
        for program in store.top_programs(2, scope=1):
            self.assertIn(program.id, island1)
        # No cross-island leakage in either direction.
        self.assertFalse(_ids(store.top_programs(5, scope=0)) & island1)
        self.assertFalse(_ids(store.top_programs(5, scope=1)) & island0)

    def test_out_of_range_scope_raises_indexerror(self):
        """donor: tracking.py:254.

        Pinned as observed: top_programs does NOT apply `% num_islands`, unlike
        population()/native_select()/island_fitnesses(). See module docstring.
        """
        store = _store()

        with self.assertRaises(IndexError):
            store.top_programs(5, scope=10)

    def test_empty_island_yields_no_top_programs(self):
        """donor: tracking.py:260"""
        store = _store()

        self.assertEqual(list(store.top_programs(5, scope=0)), [])


class TestIslandSelectionThroughWrapper(_IslandsWrapperTestCase):
    """Donor: tracking.py:190, ratios 2/3/4/5/6/7/9/10, child_placement.py:104."""

    def test_inspirations_come_from_the_parents_island(self):
        """donor: tracking.py:190 -- upstream calls the private
        _sample_inspirations; the wrapper reaches the same confinement contract
        through native_select."""
        store = _store()
        _seed(store, 0, [("i0a", 0.9), ("i0b", 0.7)])
        _seed(store, 1, [("i1a", 0.8), ("i1b", 0.6)], start_iteration=2)

        selection = store.native_select(0, num_inspirations=5)

        self.assertEqual(selection.source_scope, 0)
        for inspiration in selection.inspirations:
            self.assertEqual(inspiration.metadata.get("island"), 0)

    def test_selection_parent_and_inspirations_share_the_requested_island(self):
        """donor: ratios.py:108"""
        store = _store()
        _seed(store, 0, [(f"r0_{i}", 0.5 + i * 0.01) for i in range(20)])

        selection = store.native_select(0, num_inspirations=5)

        self.assertEqual(selection.parent.metadata.get("island"), 0)
        self.assertEqual(selection.source_scope, 0)
        for inspiration in selection.inspirations:
            self.assertEqual(inspiration.metadata.get("island"), 0)

    def test_different_islands_draw_from_disjoint_parent_sets(self):
        """donor: ratios.py:122"""
        store = _store()
        island0 = _seed(store, 0, [(f"d0_{i}", 0.5 + i * 0.01) for i in range(20)])
        island1 = _seed(store, 1, [(f"d1_{i}", 0.6 + i * 0.01) for i in range(15)],
                        start_iteration=20)

        drawn0 = {store.native_select(0, num_inspirations=0).parent.id
                  for _ in range(50)}
        drawn1 = {store.native_select(1, num_inspirations=0).parent.id
                  for _ in range(50)}

        self.assertTrue(drawn0 <= island0)
        self.assertTrue(drawn1 <= island1)
        self.assertFalse(drawn0 & drawn1)

    def test_exploitation_mode_still_returns_a_live_program(self):
        """donor: ratios.py:140"""
        store = _store()
        _seed(store, 0, [(f"x_{i}", 0.5 + i * 0.01) for i in range(20)])
        store.config.exploration_ratio = 0.0
        store.config.exploitation_ratio = 1.0

        for _ in range(20):
            parent = store.native_select(0, num_inspirations=0).parent
            self.assertIsNotNone(store.get(parent.id))

    def test_exploration_mode_spreads_across_the_island(self):
        """donor: ratios.py:157"""
        store = _store()
        survivors = _seed(store, 0, [(f"e_{i}", 0.5 + i * 0.01) for i in range(20)])
        store.config.exploration_ratio = 1.0
        store.config.exploitation_ratio = 0.0

        drawn = [store.native_select(0, num_inspirations=0).parent.id
                 for _ in range(200)]

        self.assertTrue(set(drawn) <= survivors)
        if len(survivors) > 1:
            self.assertGreater(
                len(set(drawn)), 1, "exploration should not collapse onto one parent"
            )

    def test_weighted_mode_favors_higher_fitness_than_the_island_mean(self):
        """donor: ratios.py:186"""
        store = _store()
        _seed(store, 0, [(f"w_{i}", 0.5 + i * 0.02) for i in range(20)])
        store.config.exploration_ratio = 0.0
        store.config.exploitation_ratio = 0.0

        island = list(store.population(0))
        baseline = sum(p.metrics["combined_score"] for p in island) / len(island)
        sampled = [store.native_select(0, num_inspirations=0).parent
                   for _ in range(200)]
        sampled_mean = sum(p.metrics["combined_score"] for p in sampled) / len(sampled)

        self.assertGreaterEqual(sampled_mean, baseline)

    def test_empty_island_selection_still_returns_a_parent(self):
        """donor: ratios.py:223"""
        store = _store()
        _seed(store, 0, [("f0", 0.5), ("f1", 0.6)])

        selection = store.native_select(1, num_inspirations=0)

        self.assertIsNotNone(selection.parent)

    def test_empty_island_fallback_reports_the_real_source_scope(self):
        """donor: child_placement.py:104 -- the Selection pair is what lets the
        caller place the child on the requested island while knowing the parent
        actually came from elsewhere."""
        store = _store()
        _seed(store, 0, [("fb0", 0.5), ("fb1", 0.6)])

        selection = store.native_select(1, num_inspirations=5)

        self.assertEqual(selection.source_scope, 0)
        self.assertEqual(selection.target_scope, 1)
        self.assertEqual(selection.parent.metadata.get("island"), 0)

    def test_single_program_island_returns_it_with_no_inspirations(self):
        """donor: ratios.py:267"""
        store = _store()
        store.add(_program("solo", 0.5), iteration=0, target_scope=0)

        selection = store.native_select(0, num_inspirations=5)

        self.assertEqual(selection.parent.id, "solo")
        self.assertEqual(len(selection.inspirations), 0)

    def test_scope_beyond_island_count_wraps_around(self):
        """donor: ratios.py:283.

        Pinned as observed: native_select DOES apply `% num_islands`, unlike
        top_programs. See module docstring.
        """
        store = _store()
        store.add(_program("wrap0", 0.5), iteration=0, target_scope=0)

        selection = store.native_select(3, num_inspirations=0)

        self.assertEqual(selection.parent.metadata.get("island"), 0)
        self.assertEqual(selection.target_scope, 0)


class TestIslandMigrationThroughWrapper(_IslandsWrapperTestCase):
    """Donor: migration.py 1/199/214/256, no_duplicates.py 1/2/3/4/5/6.

    Migration is driven only through repeated end_generation(); no test here
    assigns island_generations or calls migrate_programs() directly.
    """

    @staticmethod
    def _migration_store(**overrides) -> IslandsStore:
        values = dict(num_islands=4, migration_interval=2, migration_rate=0.5)
        values.update(overrides)
        return _store(**values)

    def test_islands_start_empty_and_evenly_configured(self):
        """donor: migration.py:33"""
        store = _store()

        self.assertEqual(store.num_islands, 3)
        self.assertEqual(tuple(store.scopes), (0, 1, 2))
        for scope in range(3):
            self.assertEqual(len(store.population(scope)), 0)
        self.assertEqual(list(store.per_scope_bests()), [0.0, 0.0, 0.0])
        self.assertEqual([region.size for region in store.regions()], [0, 0, 0])

    def test_migration_with_empty_islands_does_not_raise(self):
        """donor: migration.py:199"""
        store = self._migration_store()
        _seed(store, 0, [("m0", 0.5)])

        try:
            _run_to_migration(store)
        except AssertionError:
            raise
        except Exception as exc:  # pragma: no cover - guarding a crash contract
            self.fail(f"migration with empty islands should not crash: {exc}")

        self.assertGreaterEqual(store.num_programs, 1)

    def test_migrants_copy_their_source_and_land_on_their_recorded_island(self):
        """donor: migration.py:214 -- the donor's per-migrant loop is vacuous when
        nothing migrates, so this asserts at least one migrant was produced."""
        store = self._migration_store()
        _seed(store, 0, [(f"c{i}", 0.5 + i * 0.1) for i in range(4)])
        originals = {p.id: p for p in store.population(None)}

        _run_to_migration(store)

        migrants = [p for p in store.population(None) if p.metadata.get("migrant")]
        self.assertTrue(migrants, "expected migration to produce at least one migrant")
        for migrant in migrants:
            source = originals[migrant.parent_id]
            self.assertEqual(migrant.code, source.code)
            self.assertEqual(migrant.metrics, source.metrics)
            self.assertIn(migrant.id, _ids(store.population(migrant.metadata["island"])))

    def test_single_island_configuration_never_grows_by_migration(self):
        """donor: migration.py:256"""
        store = _store(num_islands=1, migration_interval=2, migration_rate=0.5)
        _seed(store, 0, [("s0", 0.5)])
        before = store.num_programs

        for _ in range(6):
            store.end_generation()

        self.assertEqual(store.num_programs, before)

    def test_migration_never_produces_migrant_suffixed_ids(self):
        """donor: no_duplicates.py:49"""
        store = self._migration_store()
        _seed(store, 0, [(f"u{i}", 0.5 + i * 0.1) for i in range(4)])

        _run_to_migration(store)

        self.assertEqual(
            [p.id for p in store.population(None) if "_migrant" in p.id], []
        )

    def test_repeated_migration_rounds_do_not_grow_exponentially(self):
        """donor: no_duplicates.py:83 -- denominator is num_programs here, where
        the donor counted feature-map occupants."""
        store = self._migration_store()
        _seed(store, 0, [(f"g{i}", 0.5 + i * 0.1) for i in range(4)])

        for _ in range(5):
            before = store.num_programs
            _run_to_migration(store)
            self.assertLess(store.num_programs, max(1, before) * 3.0)

    def test_migration_preserves_program_content(self):
        """donor: no_duplicates.py:123"""
        store = self._migration_store()
        code = "def preserved():\n    return 0.85\n"
        store.add(
            _program("preserved", 0.85, code=code), iteration=0, target_scope=0
        )

        _run_to_migration(store)

        for program in store.population(None):
            self.assertEqual(program.code, code)
            self.assertEqual(program.metrics["combined_score"], 0.85)

    def test_migrants_are_recorded_on_an_island_and_total_never_shrinks(self):
        """donor: no_duplicates.py:159 -- the donor hides its real check behind
        `if len(migrant_programs) > 0`; asserted unconditionally here."""
        store = self._migration_store()
        _seed(store, 0, [(f"d{i}", 0.5 + i * 0.1) for i in range(4)])
        before = store.num_programs

        _run_to_migration(store)

        self.assertGreaterEqual(store.num_programs, before)
        migrants = [p for p in store.population(None) if p.metadata.get("migrant")]
        self.assertTrue(migrants, "expected migration to produce at least one migrant")
        for migrant in migrants:
            self.assertIn(migrant.metadata["island"], range(store.num_islands))

    def test_no_program_id_occupies_two_islands(self):
        """donor: no_duplicates.py:198 -- stated directly over island membership
        rather than the donor's feature-map scan."""
        store = self._migration_store()
        _seed(store, 0, [(f"n{i}", 0.5 + i * 0.1) for i in range(4)])

        _run_to_migration(store)

        per_island = [_ids(store.population(i)) for i in range(store.num_islands)]
        for left in range(len(per_island)):
            for right in range(left + 1, len(per_island)):
                self.assertFalse(
                    per_island[left] & per_island[right],
                    f"islands {left} and {right} share program ids",
                )

    def test_repeated_migration_leaves_ids_clean(self):
        """donor: no_duplicates.py:230 -- the donor's stated intent (same-cell
        conflicts resolve to the better program) is never actually asserted
        there; its only real check is the id scan, reproduced here."""
        store = self._migration_store()
        _seed(store, 0, [(f"k{i}", 0.5 + i * 0.1) for i in range(4)])

        _run_to_migration(store)
        _run_to_migration(store)

        self.assertEqual(
            [p.id for p in store.population(None) if "_migrant" in p.id], []
        )


class TestIslandMapElitesThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_island_map_elites.py 3/4/5/6/7.

    The donor asserts over db.island_feature_maps; per-island membership through
    population() is the wrapper-visible statement of the same contract.
    """

    def test_identical_features_on_different_islands_do_not_collide(self):
        """donor: map_elites.py:77"""
        store = _store(feature_bins=5)
        shared_code = "def identical():\n    return 1\n"
        store.add(_program("prog1", 0.5, code=shared_code),
                  iteration=0, target_scope=0)
        store.add(_program("prog2", 0.5, code=shared_code),
                  iteration=1, target_scope=1)

        self.assertIsNotNone(store.get("prog1"))
        self.assertIsNotNone(store.get("prog2"))
        self.assertIn("prog1", _ids(store.population(0)))
        self.assertIn("prog2", _ids(store.population(1)))

    def test_better_program_replaces_the_weaker_one_in_the_same_cell(self):
        """donor: map_elites.py:94 -- strengthened, and it had to be.

        The donor hides its real assertions behind
        ``if len(feature_map_values_before) == len(feature_map_values_after)``
        (:129) plus a tautological ``if identical_code == identical_code`` (:132).
        Identical code does NOT guarantee an identical cell -- the default
        ``diversity`` dimension is computed against the rest of the population --
        so the cell grows, the guard is false, and the donor's assertions never
        execute. It passes vacuously upstream.

        Here the collision is forced with a metric-backed feature dimension, so
        replacement is genuinely exercised. One correction to the donor's
        unreached claim: the evicted program leaves the ISLAND SET but survives in
        ``_db.programs``, so its ``assertIsNone(db.get("prog1"))`` would in fact
        have failed had it ever run.
        """
        store = _store(feature_dimensions=["cell"], feature_bins=5)
        cell = {"combined_score": 0.2, "cell": 0.5}
        store.add(_program("weak", 0.2, metrics=dict(cell)),
                  iteration=0, target_scope=0)
        self.assertEqual(_ids(store.population(0)), {"weak"})

        store.add(_program("strong", 0.9, metrics={"combined_score": 0.9,
                                                   "cell": 0.5}),
                  iteration=1, target_scope=0)

        self.assertEqual(_ids(store.population(0)), {"strong"})
        self.assertEqual(store.per_scope_bests()[0], 0.9)
        self.assertIsNotNone(
            store.get("weak"), "eviction removes from the island, not from programs"
        )

    def test_global_best_program_spans_islands(self):
        """donor: map_elites.py:140"""
        store = _store()
        store.add(_program("low", 0.3), iteration=0, target_scope=0)
        store.add(_program("high", 0.95), iteration=1, target_scope=1)

        self.assertEqual(store.best_program().id, "high")

    def test_no_migrant_suffix_ids_are_ever_generated(self):
        """donor: map_elites.py:156"""
        store = _store()
        _seed(store, 0, [("ms0", 0.5), ("ms1", 0.6)])
        _seed(store, 1, [("ms2", 0.7)], start_iteration=2)

        self.assertEqual(
            [p.id for p in store.population(None) if "_migrant" in p.id], []
        )

    def test_checkpoint_round_trip_preserves_per_island_placement(self):
        """donor: map_elites.py:173"""
        import tempfile

        store = _store()
        _seed(store, 0, [("cp0", 0.5), ("cp1", 0.6)])
        _seed(store, 1, [("cp2", 0.7)], start_iteration=2)
        before = [_ids(store.population(i)) for i in range(store.num_islands)]

        with tempfile.TemporaryDirectory() as tmp:
            store.save(tmp, iteration=7)
            restored = _store()
            restored.load(tmp)

        after = [_ids(restored.population(i)) for i in range(restored.num_islands)]
        self.assertEqual(after, before)
        self.assertEqual(restored.num_programs, store.num_programs)


if __name__ == "__main__":
    unittest.main()
