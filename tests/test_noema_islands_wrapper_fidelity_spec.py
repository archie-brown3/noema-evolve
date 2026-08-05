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
    ``IndexError``. The wrap side is pinned here
    (``test_scope_beyond_island_count_wraps_around``); the ``IndexError`` side is
    pinned by the adapter-routed donor
    ``AdapterRouted_test_island_tracking_TestIslandTracking::
    test_invalid_island_index_handling`` since the Stage 6 reduction. This
    asymmetry is recorded as current behavior, not as an intended contract.

Reduced at 0188 Stage 6
    Fourteen hand-translations were deleted here. Each was kept only if it
    failed one of the two reduction conditions: (a) its donor counterpart runs
    green through the strict adapter AND itself kills at least one matrix
    mutant, and (b) it is not the sole population killer of any mutant in the
    run-2 matrix. A test whose only surviving cover would have been a donor that
    kills nothing was NOT deleted -- redundancy with a placebo is not
    redundancy. The per-deletion ledger is in the Stage 6 stage note; the
    authority for (b) is ``docs/fidelity/mutation-matrix.csv``.
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

    def test_resuming_a_pre_repair_checkpoint_keeps_migration_cadence(self):
        """The one path where pre- and post-repair code meet.

        ``load()`` restores island_generations from checkpoint metadata
        (openevolve/database.py:668), so a checkpoint written before the repair
        carries the divergent [G, 0, 0] vector into repaired code, which then
        advances it to [G+1, 1, 1]. Because only max() is behavioural and max
        continues from G, the firing schedule is unperturbed -- which is what
        makes "no completed results are invalidated" true across a resume.
        """
        import tempfile

        interval = 5
        divergent = _store(num_islands=3, migration_interval=interval)
        divergent.add(_program("resume_seed", 0.5), iteration=0, target_scope=0)
        # Reproduce exactly what a pre-repair run would have persisted.
        divergent._db.island_generations = [3, 0, 0]

        with tempfile.TemporaryDirectory() as tmp:
            divergent.save(tmp, iteration=3)
            resumed = _store(num_islands=3, migration_interval=interval)
            resumed.load(tmp)

            self.assertEqual(resumed._db.island_generations, [3, 0, 0])

            fired = [sweep for sweep in range(6) if resumed.end_generation()]

        # max was 3 on load, so the interval is reached two sweeps later.
        self.assertEqual(fired, [1])


class TestIslandPlacementThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_island_child_placement.py 1/2/4/5/6/8,
    test_island_parent_consistency.py:132, test_island_migration.py:47."""

    def test_child_inherits_parent_island_when_no_target_specified(self):
        """donor: child_placement.py:24

        Parent on island 2, NOT island 0. Upstream's add() has two branches that
        both answer 0 for a parent on island 0 (openevolve/database.py:239-246
        inherit vs :247-257 current_island fallback, and current_island is 0 and
        is never advanced by noema — spec §5 §5c). With the parent on island 2
        the inherit branch answers 2 and the fallback answers 0, so the
        assertion finally discriminates. This test was killed by ZERO mutants in
        matrix run 1, exactly as spec §5 §5e predicted."""
        store = _store()
        store.add(_program("parent_0", 0.5), iteration=0, target_scope=2)

        child = _program("child_0", 0.6, parent_id="parent_0")
        store.add(child, iteration=1)

        self.assertEqual(child.metadata.get("island"), 2)
        self.assertIn("child_0", _ids(store.population(2)))
        self.assertNotIn("child_0", _ids(store.population(0)))

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


class TestIslandBestTrackingThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_island_tracking.py 1/2/3/4/5/6/8/12.

    Island-best is asserted via per_scope_bests(), which routes through
    store.fitness == get_fitness_score -- the same scalar upstream's
    island_best_programs uses. top_programs ranks by metric average and would
    agree only by coincidence on these fixtures.
    """

    def test_better_program_raises_the_island_best(self):
        """donor: tracking.py:50

        Island 1 carries a STRICTLY HIGHER program throughout. Without it the
        assertion cannot tell a per-island best from the global best (0188
        Stage 5 matrix, Rule 1)."""
        store = _store()
        store.add(_program("elsewhere", 0.95), iteration=0, target_scope=1)
        store.add(_program("mediocre", 0.5), iteration=1, target_scope=0)
        self.assertEqual(store.per_scope_bests()[0], 0.5)

        store.add(_program("better", 0.8), iteration=2, target_scope=0)

        self.assertEqual(store.per_scope_bests()[0], 0.8)
        self.assertEqual(store.per_scope_bests()[1], 0.95)

    def test_worse_program_does_not_lower_the_island_best(self):
        """donor: tracking.py:62 (island 1 higher throughout — see above)"""
        store = _store()
        store.add(_program("elsewhere", 0.95), iteration=0, target_scope=1)
        store.add(_program("good", 0.8), iteration=1, target_scope=0)

        store.add(_program("worse", 0.3), iteration=2, target_scope=0)

        self.assertEqual(store.per_scope_bests()[0], 0.8)
        self.assertEqual(store.per_scope_bests()[1], 0.95)

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
        store.add(_program("cs_elsewhere", 0.95), iteration=0, target_scope=1)
        store.add(
            _program("cs1", 0.0, metrics={"score": 0.5, "other": 0.3,
                                          "combined_score": 0.4}),
            iteration=1, target_scope=0,
        )
        self.assertEqual(store.per_scope_bests()[0], 0.4)

        # Lower `score`, higher `combined_score` -- combined_score must win.
        store.add(
            _program("cs2", 0.0, metrics={"score": 0.3, "other": 0.7,
                                          "combined_score": 0.5}),
            iteration=2, target_scope=0,
        )

        self.assertEqual(store.per_scope_bests()[0], 0.5)
        self.assertEqual(store.per_scope_bests()[1], 0.95)

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

    def test_empty_island_yields_no_top_programs(self):
        """donor: tracking.py:260"""
        store = _store()
        # Island 1 populated: an empty island 0 must stay empty even when the
        # database is not (0188 Stage 5 matrix, Rule 1).
        _seed(store, 1, [("tp_off", 0.9)])

        self.assertEqual(list(store.top_programs(5, scope=0)), [])
        self.assertEqual(_ids(store.population(0)), set())


class TestIslandSelectionThroughWrapper(_IslandsWrapperTestCase):
    """Donor: tracking.py:190, ratios 2/3/4/5/6/7/9/10, child_placement.py:104."""

    def test_inspirations_come_from_the_parents_island(self):
        """donor: tracking.py:190 -- upstream calls the private
        _sample_inspirations; the wrapper reaches the same confinement contract
        through native_select."""
        store = _store()
        _seed(store, 0, [(f"i0_{i}", 0.5 + i * 0.01) for i in range(12)])
        _seed(store, 1, [("i1a", 0.8), ("i1b", 0.6)], start_iteration=12)

        # Exactly two, not upstream's default of five: the requested inspiration
        # count is part of the contract (0188 Stage 5 matrix, Rule 1).
        selection = store.native_select(0, num_inspirations=2)

        self.assertEqual(selection.source_scope, 0)
        self.assertEqual(len(selection.inspirations), 2)
        for inspiration in selection.inspirations:
            self.assertEqual(inspiration.metadata.get("island"), 0)
        # Confinement stated positively: island 0's cohort excludes island 1's
        # members (0188 Stage 5 matrix, Rule 1).
        self.assertEqual(_ids(store.population(0)) & {"i1a", "i1b"}, set())

    def test_selection_parent_and_inspirations_share_the_requested_island(self):
        """donor: ratios.py:108"""
        store = _store()
        _seed(store, 0, [(f"r0_{i}", 0.5 + i * 0.01) for i in range(20)])
        _seed(store, 1, [("r1_off", 0.99)], start_iteration=20)

        selection = store.native_select(0, num_inspirations=3)

        self.assertEqual(selection.parent.metadata.get("island"), 0)
        self.assertEqual(selection.source_scope, 0)
        self.assertEqual(len(selection.inspirations), 3)
        for inspiration in selection.inspirations:
            self.assertEqual(inspiration.metadata.get("island"), 0)
        self.assertNotIn("r1_off", _ids(store.population(0)))

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

        # Preconditions: empty draw sets would make every claim below trivial.
        self.assertTrue(drawn0)
        self.assertTrue(drawn1)
        self.assertTrue(drawn0 <= island0)
        self.assertTrue(drawn1 <= island1)
        self.assertFalse(drawn0 & drawn1)

    def test_exploitation_mode_still_returns_a_live_program(self):
        """donor: ratios.py:140"""
        store = _store()
        # Island 1 first, so `survivors` is computed with it already present:
        # a scope-ignoring population() would then include x_off and trip the
        # assertion below (0188 Stage 5 matrix, Rule 1).
        _seed(store, 1, [("x_off", 0.99)])
        survivors = _seed(store, 0, [(f"x_{i}", 0.5 + i * 0.01) for i in range(20)],
                          start_iteration=1)
        self.assertNotIn("x_off", survivors)
        store.config.exploration_ratio = 0.0
        store.config.exploitation_ratio = 1.0

        for _ in range(20):
            selection = store.native_select(0, num_inspirations=0)
            self.assertIsNotNone(store.get(selection.parent.id))
            # Zero requested means zero delivered, not upstream's default five
            # (0188 Stage 5 matrix, Rule 1).
            self.assertEqual(len(selection.inspirations), 0)
            self.assertIn(selection.parent.id, survivors)

    def test_exploration_mode_spreads_across_the_island(self):
        """donor: ratios.py:157"""
        store = _store()
        _seed(store, 1, [("e_off", 0.99)])
        survivors = _seed(store, 0, [(f"e_{i}", 0.5 + i * 0.01) for i in range(20)],
                          start_iteration=1)
        self.assertNotIn("e_off", survivors)
        store.config.exploration_ratio = 1.0
        store.config.exploitation_ratio = 0.0

        # Precondition, asserted rather than branched on: the donor's point is
        # that exploration spreads, which is only meaningful with >1 candidate.
        self.assertGreater(len(survivors), 1)

        selections = [store.native_select(0, num_inspirations=2) for _ in range(200)]
        drawn = [selection.parent.id for selection in selections]

        self.assertTrue(set(drawn) <= survivors)
        for selection in selections:
            self.assertEqual(len(selection.inspirations), 2)
        self.assertGreater(
            len(set(drawn)), 1, "exploration should not collapse onto one parent"
        )

    def test_empty_island_selection_still_returns_a_parent(self):
        """donor: ratios.py:223"""
        store = _store()
        _seed(store, 0, [("f0", 0.5), ("f1", 0.6)])

        selection = store.native_select(1, num_inspirations=0)

        self.assertIsNotNone(selection.parent)
        # The fallback reports the REAL source island, not the requested target.
        # Without these two the test cannot tell the fallback from a selection
        # that never left island 1 (0188 Stage 5 matrix, Rule 1).
        self.assertEqual(selection.source_scope, 0)
        self.assertEqual(selection.target_scope, 1)

    def test_single_program_island_returns_it_with_no_inspirations(self):
        """donor: ratios.py:267"""
        store = _store()
        store.add(_program("solo", 0.5), iteration=0, target_scope=0)
        # Island 1 is populated, so "no inspirations" is a CONFINEMENT claim
        # rather than "the database is empty" (0188 Stage 5 matrix, Rule 1).
        _seed(store, 1, [(f"co_{i}", 0.5 + i * 0.01) for i in range(3)], start_iteration=1)

        selection = store.native_select(0, num_inspirations=5)

        self.assertEqual(selection.parent.id, "solo")
        self.assertEqual(len(selection.inspirations), 0)
        self.assertEqual(_ids(store.population(0)), {"solo"})

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

        migrated = [store.end_generation() for _ in range(6)]

        # Migration really did fire — otherwise "never grows by migration" is
        # true for the uninteresting reason (0188 Stage 5 matrix, Rule 1).
        self.assertTrue(any(migrated), "no migration fired; the claim is vacuous")
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

        survivors = list(store.population(None))
        self.assertTrue(survivors, "loop below would be vacuous")
        for program in survivors:
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
        # Precondition: disjointness is trivial unless migration actually spread
        # programs across at least two islands.
        self.assertGreaterEqual(
            sum(1 for island in per_island if island), 2,
            "expected migration to populate a second island",
        )
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

    def test_no_migrant_suffix_ids_are_ever_generated(self):
        """donor: map_elites.py:156"""
        store = _store()
        _seed(store, 0, [("ms0", 0.5), ("ms1", 0.6)])
        _seed(store, 1, [("ms2", 0.7)], start_iteration=2)

        self.assertEqual(
            [p.id for p in store.population(None) if "_migrant" in p.id], []
        )
        # The count is the sharp half of the claim: a migrant-suffixed COPY
        # would leave the ids clean and still inflate the database
        # (0188 Stage 5 matrix, Rule 1).
        self.assertEqual(store.num_programs, 3)

    def test_checkpoint_round_trip_preserves_per_island_placement(self):
        """donor: map_elites.py:173"""
        import tempfile

        store = _store()
        _seed(store, 0, [("cp0", 0.5), ("cp1", 0.6)])
        _seed(store, 1, [("cp2", 0.7)], start_iteration=2)
        before = [_ids(store.population(i)) for i in range(store.num_islands)]
        # PER-island placement: if every island reported the same membership the
        # round trip would preserve nothing worth naming (Stage 5 matrix, Rule 1).
        self.assertNotEqual(before[0], before[1])

        with tempfile.TemporaryDirectory() as tmp:
            store.save(tmp, iteration=7)
            restored = _store()
            restored.load(tmp)

        after = [_ids(restored.population(i)) for i in range(restored.num_islands)]
        self.assertEqual(after, before)
        self.assertEqual(restored.num_programs, store.num_programs)
        # The checkpoint records the iteration it was written at.
        self.assertEqual(restored.last_iteration, 7)


class TestIslandConcurrencyThroughWrapper(_IslandsWrapperTestCase):
    """Donor: test_concurrent_island_access.py:50 -- a THIRD vacuous donor,
    found during 0188 Stage 1 triage and not listed in the §4 dossier.

    The donor test contains no assertion of any kind: it prints
    "Race condition detected" / "Successfully reproduced" and returns. It
    passes against any implementation, including one that raises on every
    call. What it documents is upstream's GitHub issue #246 -- concurrent
    workers each write ``db.current_island`` before calling ``db.sample()``,
    so one worker's island leaks into another's sample.

    Reproducing that race is not a contract Noema owes. Noema's answer is
    structural: ``IslandsStore`` has no mutable current-island state at all,
    the target island is an ARGUMENT to ``native_select``. This test states
    that positively and discriminatingly -- it fails if selection ever returns
    a parent from an island other than the one the caller asked for under
    concurrent access, which is exactly the bug #246 describes.
    """

    def test_concurrent_selection_never_crosses_island_boundaries(self):
        import concurrent.futures

        store = _store(num_islands=5)
        for island in range(5):
            _seed(
                store,
                island,
                [(f"i{island}_p{i}", 0.1 * i + 0.05 * island) for i in range(4)],
                start_iteration=island * 4,
            )
        populated = [i for i in range(5) if store.population(i)]
        self.assertEqual(populated, list(range(5)), "fixture must populate every island")

        def select(island: int):
            selection = store.native_select(island, 2)
            return island, selection

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            outcomes = list(pool.map(select, [i % 5 for i in range(40)]))

        self.assertEqual(len(outcomes), 40)
        for requested, selection in outcomes:
            members = _ids(store.population(requested))
            self.assertEqual(selection.target_scope, requested)
            self.assertIn(
                selection.parent.id,
                members,
                f"parent {selection.parent.id} is not on requested island {requested}",
            )
            for inspiration in selection.inspirations:
                self.assertIn(inspiration.id, members)

    def test_selection_target_is_an_argument_not_shared_mutable_state(self):
        """The structural reason the race above cannot occur: two selections
        against different islands do not interfere, and nothing on the store
        records a 'current' island between calls."""
        store = _store(num_islands=3)
        for island in range(3):
            _seed(store, island, [(f"s{island}", 0.5 + 0.1 * island)],
                  start_iteration=island)

        first = store.native_select(0, 0)
        second = store.native_select(2, 0)
        third = store.native_select(0, 0)

        self.assertEqual(
            [first.target_scope, second.target_scope, third.target_scope], [0, 2, 0]
        )
        self.assertEqual(first.parent.id, third.parent.id)
        self.assertFalse(
            [name for name in vars(store) if "current" in name],
            "IslandsStore must hold no current-island state (upstream issue #246)",
        )


class TestMutationMatrixCoverageHoles(_IslandsWrapperTestCase):
    """0188 Stage 5, Rule 2 fills.

    Matrix run 1 left these wrapper members killed by ZERO tests in the whole
    collection — behaviour the suite asserted nowhere. Each test below names the
    mutant it exists to kill; the mutant ids are the harness keys in
    ``tests/mutation/mutants.py``. Grounding: vault note "0188 OpenEvolve
    Fidelity Spec §5 — Mutation Matrix — 2026-08-05" §2a/§2b, and §2e which
    predicted three of them (store_artifacts, capabilities, fitness) by grep
    before the harness existed.

    These are not donor translations. They are Noema's own pins on wrapper
    members whose upstream semantic claim was unverified.
    """

    def test_capabilities_are_exactly_what_the_store_serves(self):
        """kills islands.capabilities.overclaim.

        Every existing `capabilities` assertion in the suite is an `assertIn`
        or a subset check, and the runtime gate at base.py:150-154 tests
        `required - capabilities` — so ADDING a capability can never trip it.
        Only an exact-set assertion can (spec §2e.2)."""
        self.assertEqual(
            IslandsStore.capabilities,
            frozenset(
                {
                    "population",
                    "elites",
                    "fitness",
                    "code",
                    "sampling_weights",
                    "regions",
                    "native_stock_selection",
                }
            ),
        )

    def test_elites_are_the_fittest_of_the_scope_in_descending_order(self):
        """kills islands.elites.worst_first."""
        store = _store()
        survivors = _seed(store, 0, [(f"el_{i}", 0.1 * i) for i in range(1, 9)])
        self.assertGreater(len(survivors), 1, "ordering claim needs >1 survivor")

        elites = store.elites(0)
        fitnesses = [store.fitness(program) for program in elites]

        self.assertEqual(fitnesses, sorted(fitnesses, reverse=True))
        self.assertEqual(fitnesses[0], max(store.island_fitnesses(0)))

    def test_snapshot_limit_selects_the_top_n_not_an_arbitrary_n(self):
        """kills islands.snapshot.limit_before_sort."""
        store = _store()
        # Ascending insert order, so an unsorted head is the WORST two.
        _seed(store, 0, [(f"sn_{i}", 0.1 * i) for i in range(1, 9)])
        best_two = sorted(store.island_fitnesses(0), reverse=True)[:2]

        snapshot = store.snapshot(scope=0, limit=2)

        self.assertEqual(len(snapshot.top_programs), 2)
        self.assertEqual([view.fitness for view in snapshot.top_programs], best_two)

    def test_scope_takes_precedence_over_the_legacy_island_alias(self):
        """kills islands.top_programs.island_wins.

        `scope` is the neutral spelling; `island` survives only as the legacy
        alias. The precedence is observable only when both are passed and
        differ, which no production caller does — so nothing pinned it."""
        store = _store()
        _seed(store, 0, [("pref_0", 0.5)])
        _seed(store, 1, [("pref_1", 0.9)], start_iteration=1)

        top = store.top_programs(5, scope=0, island=1)

        self.assertEqual([p.id for p in top], ["pref_0"])

    def test_steps_per_generation_survives_a_checkpoint_round_trip(self):
        """kills islands.state_dict.empty AND islands.load_state_dict.ignore_state.

        Both are invisible unless the value is first set to something other
        than the `num_islands` default that `load_state_dict` falls back to
        (spec §2a) — which is why one test closes two holes."""
        store = _store(num_islands=3)
        self.assertEqual(store.steps_per_generation, 3)
        store.steps_per_generation = 7

        state = store.state_dict()
        restored = _store(num_islands=3)
        restored.load_state_dict(state)

        self.assertEqual(state, {"steps_per_generation": 7})
        self.assertEqual(restored.steps_per_generation, 7)

    def test_all_fitnesses_spans_every_island(self):
        """kills database.all_fitnesses.island_zero."""
        store = _store()
        _seed(store, 0, [("af0", 0.2)])
        _seed(store, 1, [("af1", 0.5)], start_iteration=1)
        _seed(store, 2, [("af2", 0.8)], start_iteration=2)

        self.assertEqual(sorted(store.all_fitnesses()), [0.2, 0.5, 0.8])

    def test_get_returns_none_for_an_unknown_id_and_never_substitutes(self):
        """kills database.get.best_fallback."""
        store = _store()
        _seed(store, 0, [("known", 0.9)])

        self.assertIsNone(store.get("no_such_program"))
        self.assertEqual(store.get("known").id, "known")

    def test_sample_from_island_honours_the_requested_inspiration_count(self):
        """kills database.sample_from_island.drop_inspirations.

        Reached DIRECTLY, not through native_select: IslandsStore.native_select
        calls ``self._db.sample_from_island`` (islands.py:76), so the wrapper's
        own forwarding method is unreachable from the selection path and no
        selection test can pin it. Upstream defaults the count to 5
        (openevolve/database.py:396-397), so dropping the kwarg is silent
        wherever a test happens to ask for 5."""
        store = _store()
        _seed(store, 0, [(f"sf_{i}", 0.5 + i * 0.01) for i in range(12)])

        _, inspirations = store.sample_from_island(0, num_inspirations=2)

        self.assertEqual(len(inspirations), 2)

    def test_artifacts_reach_the_upstream_store_and_read_back(self):
        """kills database.store_artifacts.noop.

        The only artifact assertion in the suite before this one read a test
        fixture's own dict, never SubstrateDatabase.store_artifacts, so a
        no-op forward was completely invisible (spec §2e.1)."""
        store = _store()
        _seed(store, 0, [("art0", 0.5)])

        store.store_artifacts("art0", {"stderr": "boom"})

        self.assertEqual(store._db.get_artifacts("art0"), {"stderr": "boom"})


if __name__ == "__main__":
    unittest.main()
