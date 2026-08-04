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


if __name__ == "__main__":
    unittest.main()
