"""
Task 0132 — conformance harness certifying noema's null substrate (IslandsStore /
SubstrateDatabase) against upstream OpenEvolve's ProgramDatabase.

PROVENANCE
----------
The scenarios below are *derived from* (not copied out of) OpenEvolve's own test
suite, which is Apache-2.0 licensed:

    Copyright the OpenEvolve authors. Licensed under the Apache License 2.0.
    Source tree pinned at commit 80945ed82886d5c4ff2f3d22436765d50cb61266
    (vendored read-only at <repo>/.openevolve-upstream).

Each scenario names the upstream test it derives from in its docstring. The pin
is *enforced* by `TestProvenancePin` below, not merely documented: if
`.openevolve-upstream` moves off 80945ed, or the installed `openevolve` package
stops being byte-identical to the vendored tree, the harness fails loudly rather
than silently certifying against a different upstream.

METHOD
------
Paired execution. Every scenario is a script of store operations run twice:
once against a raw `ProgramDatabase` and once against noema's `IslandsStore`.
Both sides are compared on
  (a) the per-call observable trace (sampled parent/inspiration ids, top-n, best),
  (b) a canonicalised dump of final internal state.
`ProgramDatabase.__init__` reseeds the *global* `random` module
(.openevolve-upstream/openevolve/database.py:169-173), so each side is
constructed immediately before its script runs to make sampling deterministic.

Program ids are canonicalised because `migrate_programs()` mints migrant copies
with unseeded `uuid.uuid4()` (database.py:1852) — raw id equality would fail for
a reason that is not a faithfulness defect.
"""

import filecmp
import os
import subprocess
import sys
import tempfile
import unittest

import pytest
from openevolve.config import DatabaseConfig
from openevolve.database import Program, ProgramDatabase
from openevolve.utils.metrics_utils import get_fitness_score

from noema.substrates.islands import IslandsStore

UPSTREAM_PIN = "80945ed82886d5c4ff2f3d22436765d50cb61266"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM_DIR = os.path.join(REPO_ROOT, ".openevolve-upstream")


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #


class TestProvenancePin(unittest.TestCase):
    def test_vendored_upstream_is_at_pinned_commit(self):
        head = subprocess.check_output(
            ["git", "-C", UPSTREAM_DIR, "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertEqual(head, UPSTREAM_PIN)

    def test_installed_openevolve_matches_vendored_tree(self):
        """The whole equivalence study rests on `import openevolve` resolving to
        the same code as the vendored pin. Assert it instead of assuming it."""
        import openevolve

        installed = os.path.dirname(openevolve.__file__)
        vendored = os.path.join(UPSTREAM_DIR, "openevolve")
        mismatches = []
        for root, dirs, files in os.walk(vendored):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(root, name), vendored)
                other = os.path.join(installed, rel)
                if not os.path.exists(other) or not filecmp.cmp(
                    os.path.join(root, name), other, shallow=False
                ):
                    mismatches.append(rel)
        self.assertEqual(mismatches, [])


# --------------------------------------------------------------------------- #
# harness plumbing
# --------------------------------------------------------------------------- #


def _config(**overrides) -> DatabaseConfig:
    defaults = dict(
        num_islands=3,
        population_size=100,
        archive_size=20,
        feature_dimensions=["complexity", "diversity"],
        feature_bins=4,
        migration_interval=2,
        migration_rate=0.5,
        random_seed=42,
        db_path=None,
        embedding_model=None,
        novelty_llm=None,
    )
    defaults.update(overrides)
    return DatabaseConfig(**defaults)


def _program(pid: str, code: str, score: float, parent_id=None) -> Program:
    return Program(
        id=pid,
        code=code,
        language="python",
        parent_id=parent_id,
        metrics={"combined_score": score},
    )


class _UpstreamAdapter:
    """Drives a bare ProgramDatabase the way upstream's process_parallel does."""

    def __init__(self, config: DatabaseConfig):
        self.raw = ProgramDatabase(config)

    def add(self, program, iteration=None, island=None):
        # upstream: process_parallel.py:565-568 always passes target_island
        return self.raw.add(program, iteration=iteration, target_island=island)

    def sample(self, island, n):
        # upstream: process_parallel.py:806-808
        parent, insp = self.raw.sample_from_island(island, num_inspirations=n)
        return parent, list(insp)

    def top(self, n, island=None):
        return self.raw.get_top_programs(n, island_idx=island)

    def best(self):
        return self.raw.get_best_program()

    def save(self, path, iteration=0):
        self.raw.save(path, iteration)

    def load(self, path):
        self.raw.load(path)


class _NoemaAdapter:
    """Drives the same primitives through noema's IslandsStore wrapper."""

    def __init__(self, config: DatabaseConfig):
        self.store = IslandsStore(config)
        self.raw = self.store._db

    def add(self, program, iteration=None, island=None):
        # noema: controller.py:832 -> IslandsStore.add(target_scope=island)
        return self.store.add(program, iteration=iteration, target_scope=island)

    def sample(self, island, n):
        # noema: controller.py:433 -> IslandsStore.native_select
        selection = self.store.native_select(island, num_inspirations=n)
        return selection.parent, list(selection.inspirations)

    def top(self, n, island=None):
        return list(self.store.top_programs(n, scope=island))

    def best(self):
        return self.store.best_program()

    def save(self, path, iteration=0):
        self.store.save(path, iteration)

    def load(self, path):
        self.store.load(path)


def _canon_id(db: ProgramDatabase, pid):
    """Stable identity across runs. Explicit programs keep their scripted id;
    migrant copies get a content key because their uuid4 is unseeded."""
    if pid is None:
        return None
    program = db.programs.get(pid)
    if program is None:
        return f"<absent:{pid}>"
    if program.metadata.get("migrant"):
        return f"<migrant code={program.code!r} island={program.metadata.get('island')}>"
    return program.id


def _dump(db: ProgramDatabase):
    """Canonical, order-insensitive snapshot of every piece of archive state."""
    return {
        "islands": [sorted(_canon_id(db, p) for p in island) for island in db.islands],
        "feature_maps": [
            {k: _canon_id(db, v) for k, v in sorted(fm.items())} for fm in db.island_feature_maps
        ],
        "archive": sorted(_canon_id(db, p) for p in db.archive),
        "best_program_id": _canon_id(db, db.best_program_id),
        "island_best_programs": [_canon_id(db, p) for p in db.island_best_programs],
        "island_generations": list(db.island_generations),
        "last_migration_generation": db.last_migration_generation,
        "last_iteration": db.last_iteration,
        "current_island": db.current_island,
        "feature_stats": db._serialize_feature_stats(),
        "programs": sorted(
            (
                _canon_id(db, p.id),
                p.code,
                tuple(sorted(p.metrics.items())),
                p.metadata.get("island"),
                p.iteration_found,
                get_fitness_score(p.metrics, db.config.feature_dimensions),
            )
            for p in db.programs.values()
        ),
    }


def _trace_ids(db, programs):
    return [_canon_id(db, p.id) for p in programs]


class ConformanceCase(unittest.TestCase):
    """Runs `script(adapter)` on both sides and asserts trace + state equality."""

    def assert_conformant(self, script, config=None, **config_overrides):
        cfg = config or _config(**config_overrides)

        # constructed back-to-back so each script starts from the same RNG state
        upstream = _UpstreamAdapter(cfg)
        up_trace = script(upstream)
        up_state = _dump(upstream.raw)

        noema = _NoemaAdapter(_config(**config_overrides) if config is None else config)
        no_trace = script(noema)
        no_state = _dump(noema.raw)

        self.assertEqual(up_trace, no_trace, "observable call trace diverged")
        self.assertEqual(up_state, no_state, "final archive state diverged")
        return up_state


# --------------------------------------------------------------------------- #
# Tier 1 — behaviours that must be identical
# --------------------------------------------------------------------------- #


class TestMapElitesConformance(ConformanceCase):
    def test_cell_collision_keeps_the_better_program(self):
        """Derives from upstream tests/test_island_map_elites.py::
        test_better_program_replaces_in_island_feature_map and
        test_program_added_to_correct_island_feature_map."""

        def script(a):
            code = "def f():\n    return 42\n"
            a.add(_program("weak", code, 0.20), iteration=0, island=1)
            a.add(_program("strong", code, 0.90), iteration=1, island=1)
            a.add(_program("weaker_again", code, 0.10), iteration=2, island=1)
            db = a.raw
            return {
                "cells_island1": len(db.island_feature_maps[1]),
                "occupant": [_canon_id(db, v) for v in db.island_feature_maps[1].values()],
                "island1": sorted(_canon_id(db, p) for p in db.islands[1]),
                "best": _canon_id(db, a.best().id),
            }

        state = self.assert_conformant(script)
        self.assertEqual(state["best_program_id"], "strong")
        # the displaced occupant is dropped from the island set (database.py:333)
        self.assertNotIn("weak", state["islands"][1])

    def test_feature_coordinates_are_island_local(self):
        """Derives from tests/test_island_map_elites.py::
        test_feature_coordinate_isolation — the same code in two islands occupies
        a cell in each island's own map, not one shared grid."""

        def script(a):
            code = "def g():\n    return 1\n"
            a.add(_program("a0", code, 0.5), iteration=0, island=0)
            a.add(_program("a1", code, 0.5), iteration=1, island=2)
            db = a.raw
            return {
                "sizes": [len(fm) for fm in db.island_feature_maps],
                "keys_match": (
                    sorted(db.island_feature_maps[0]) == sorted(db.island_feature_maps[2])
                ),
            }

        self.assert_conformant(script)


class TestIslandPlacementConformance(ConformanceCase):
    def test_child_inherits_parent_island_without_explicit_target(self):
        """Derives from tests/test_island_child_placement.py::
        test_child_inherits_parent_island_when_no_target_specified and
        tests/test_island_parent_consistency.py::test_parent_child_island_consistency."""

        def script(a):
            a.add(_program("p", "def p(): pass", 0.4), iteration=0, island=2)
            a.add(
                _program("c", "def c(): return 2", 0.5, parent_id="p"),
                iteration=1,
                island=None,
            )
            db = a.raw
            return {
                "child_island": db.programs["c"].metadata["island"],
                "islands": [sorted(_canon_id(db, x) for x in i) for i in db.islands],
            }

        state = self.assert_conformant(script)
        self.assertIn("c", state["islands"][2])

    def test_explicit_target_overrides_parent_inheritance(self):
        """Derives from tests/test_island_child_placement.py::
        test_explicit_target_island_overrides_parent_inheritance — upstream's
        fix for issue #391, which noema's controller.py:832 relies on."""

        def script(a):
            a.add(_program("p", "def p(): pass", 0.4), iteration=0, island=0)
            a.add(
                _program("c", "def c(): return 2", 0.5, parent_id="p"),
                iteration=1,
                island=2,
            )
            return {"child_island": a.raw.programs["c"].metadata["island"]}

        state = self.assert_conformant(script)
        self.assertIn("c", state["islands"][2])

    def test_island_isolation_across_a_round_robin_sweep(self):
        """Derives from tests/test_island_isolation.py (which itself drives
        ProcessParallelController). Rewritten against the store: children pinned
        to island i must never leak into island j."""

        def script(a):
            for i in range(3):
                a.add(_program(f"seed{i}", f"def s{i}(): return {i}", 0.1 * i), iteration=0, island=i)
            for it in range(1, 10):
                island = it % 3
                a.add(
                    _program(f"c{it}", f"def c{it}(): return {it}", 0.05 * it, parent_id=f"seed{island}"),
                    iteration=it,
                    island=island,
                )
            db = a.raw
            return {
                "membership": {
                    _canon_id(db, pid): p.metadata["island"] for pid, p in sorted(db.programs.items())
                },
                "islands": [sorted(_canon_id(db, x) for x in i) for i in db.islands],
            }

        state = self.assert_conformant(script)
        for idx, island in enumerate(state["islands"]):
            for pid in island:
                if pid.startswith("c"):
                    self.assertEqual(int(pid[1:]) % 3, idx)


class TestSamplingConformance(ConformanceCase):
    def test_sample_from_island_sequence_is_identical(self):
        """Derives from tests/test_sample_from_island_ratios.py::
        test_sample_from_island_returns_from_correct_island and
        test_exploration_exploitation_random_ratios. noema reaches the same
        primitive through IslandsStore.native_select (islands.py:74-80)."""

        def script(a):
            for i in range(12):
                a.add(
                    _program(f"p{i:02d}", f"def p{i}():\n    return {i}\n", 0.05 * i),
                    iteration=i,
                    island=i % 3,
                )
            trace = []
            for step in range(30):
                parent, insp = a.sample(step % 3, 3)
                trace.append(
                    (
                        _canon_id(a.raw, parent.id),
                        sorted(_trace_ids(a.raw, insp)),
                        parent.metadata.get("island"),
                    )
                )
            return trace

        self.assert_conformant(script)

    def test_empty_island_falls_back_to_global_sample(self):
        """Derives from tests/test_island_child_placement.py::
        test_sample_from_empty_island_returns_fallback_parent and
        tests/test_sample_from_island_ratios.py::test_empty_island_fallback."""

        def script(a):
            for i in range(4):
                a.add(_program(f"p{i}", f"def p{i}(): return {i}", 0.2 * i), iteration=i, island=0)
            trace = []
            for _ in range(10):
                parent, insp = a.sample(1, 2)  # island 1 is empty
                trace.append((_canon_id(a.raw, parent.id), len(insp)))
            return trace

        self.assert_conformant(script)

    def test_top_programs_and_best_program_agree(self):
        """Derives from tests/test_database.py::test_get_top_programs_with_metrics
        and ::test_best_program_tracking. noema's `top_programs`
        (substrates/database.py:64) and `best_program` (:67) are pure delegation;
        this pins that they stay so."""

        def script(a):
            for i in range(9):
                a.add(
                    _program(f"p{i}", f"def p{i}():\n    return {i}\n", 0.1 * i),
                    iteration=i,
                    island=i % 3,
                )
            return {
                "global_top": _trace_ids(a.raw, a.top(5)),
                "island_top": {i: _trace_ids(a.raw, a.top(3, island=i)) for i in range(3)},
                "best": _canon_id(a.raw, a.best().id),
            }

        self.assert_conformant(script)


class TestMigrationConformance(ConformanceCase):
    def test_migration_produces_identical_archives(self):
        """Derives from tests/test_migration_no_duplicates.py::
        test_migration_target_islands_are_different,
        ::test_migrated_program_content_preserved and
        ::test_migration_skips_duplicate_code_on_target_island. Migrant ids are
        unseeded uuid4 (database.py:1852) so identity is content-canonicalised."""

        def script(a):
            for i in range(9):
                a.add(
                    _program(f"p{i}", f"def p{i}():\n    return {i}\n", 0.1 * i),
                    iteration=i,
                    island=i % 3,
                )
            # both sides drive the *same* primitive sequence here; the ordering
            # question (who calls this, and with what island_idx) is Tier 2.
            a.raw.increment_island_generation()
            a.raw.increment_island_generation()
            migrated = a.raw.should_migrate()
            if migrated:
                a.raw.migrate_programs()
            db = a.raw
            return {
                "migrated": migrated,
                "sizes": [len(i) for i in db.islands],
                "migrant_codes": sorted(
                    p.code for p in db.programs.values() if p.metadata.get("migrant")
                ),
            }

        self.assert_conformant(script)

    def test_end_generation_matches_the_upstream_primitive_sequence(self):
        """noema's `end_generation` (substrates/database.py:104-116) inlines
        upstream's process_parallel.py:618-623 sequence. With `current_island`
        pinned at 0 on both sides, a hand-rolled upstream call using the same
        (defaulted) island index must land on identical state."""

        def upstream_script(a):
            for i in range(6):
                a.add(_program(f"p{i}", f"def p{i}(): return {i}", 0.1 * i), iteration=i, island=i % 3)
            fired = []
            for _ in range(4):
                a.raw.increment_island_generation()
                if a.raw.should_migrate():
                    a.raw.migrate_programs()
                    fired.append(True)
                else:
                    fired.append(False)
            return fired

        def noema_script(a):
            for i in range(6):
                a.add(_program(f"p{i}", f"def p{i}(): return {i}", 0.1 * i), iteration=i, island=i % 3)
            return [a.store.end_generation() for _ in range(4)]

        cfg = _config()
        up = _UpstreamAdapter(cfg)
        up_fired = upstream_script(up)
        up_state = _dump(up.raw)

        no = _NoemaAdapter(_config())
        no_fired = noema_script(no)
        no_state = _dump(no.raw)

        self.assertEqual(up_fired, no_fired)
        self.assertEqual(up_state, no_state)


class TestCheckpointConformance(ConformanceCase):
    def test_save_load_round_trip_is_identical(self):
        """Derives from tests/test_grid_stability.py::
        test_feature_ranges_preserved_across_checkpoints and
        tests/test_island_map_elites.py::
        test_checkpoint_serialization_preserves_island_maps. noema's save/load
        (substrates/database.py:118-122) are pure delegation."""
        results = []
        for adapter_cls in (_UpstreamAdapter, _NoemaAdapter):
            a = adapter_cls(_config())
            for i in range(9):
                a.add(
                    _program(f"p{i}", f"def p{i}():\n    return {i}\n", 0.1 * i),
                    iteration=i,
                    island=i % 3,
                )
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "ckpt")
                a.save(path, iteration=8)
                reloaded = adapter_cls(_config())
                reloaded.load(path)
                results.append((_dump(a.raw), _dump(reloaded.raw)))
        (up_before, up_after), (no_before, no_after) = results
        self.assertEqual(up_before, no_before)
        self.assertEqual(up_after, no_after)
        # round-trip must also be lossless on the noema side
        self.assertEqual(no_before, no_after)


# --------------------------------------------------------------------------- #
# Tier 2 — known divergences. These are pinned, not papered over.
# --------------------------------------------------------------------------- #


def _generation_counters(num_islands=3, num_iterations=9, migration_interval=2):
    """Run the same iteration budget the way each controller actually drives it."""
    cfg = dict(num_islands=num_islands, migration_interval=migration_interval)

    up = _UpstreamAdapter(_config(**cfg))
    up_migrations = []
    for it in range(num_iterations):
        island = it % num_islands
        up.add(_program(f"p{it}", f"def p{it}(): return {it}", 0.05 * it), iteration=it, island=island)
        # upstream: process_parallel.py:616-623 — per iteration, with the CHILD's
        # island, and should_migrate() checked every iteration.
        child_island = up.raw.programs[f"p{it}"].metadata["island"]
        up.raw.increment_island_generation(island_idx=child_island)
        if up.raw.should_migrate():
            up.raw.migrate_programs()
            up_migrations.append(it)

    no = _NoemaAdapter(_config(**cfg))
    no_migrations = []
    for it in range(num_iterations):
        island = no.store.target_scope(it)
        no.add(_program(f"p{it}", f"def p{it}(): return {it}", 0.05 * it), iteration=it, island=island)
        # noema: controller.py:323 — only at generation boundaries, and
        # substrates/database.py:111 passes no island_idx.
        if (it + 1) % no.store.steps_per_generation == 0:
            if no.store.end_generation():
                no_migrations.append(it)

    return (up.raw.island_generations, up_migrations), (no.raw.island_generations, no_migrations)


class TestKnownDivergences(unittest.TestCase):
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "DEFECT 0132-A: noema/substrates/database.py:111 calls "
            "increment_island_generation() with no island_idx, so it always "
            "increments island_generations[current_island] and current_island is "
            "permanently 0 (nothing in noema calls set_current_island/next_island). "
            "Upstream process_parallel.py:616-618 increments the CHILD's island. "
            "island_generations is checkpointed (database.py:628) so the skew "
            "persists across resume."
        ),
    )
    def test_island_generation_counters_match_upstream(self):
        (up_gens, _), (no_gens, _) = _generation_counters()
        self.assertEqual(up_gens, no_gens)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "DEFECT 0132-B: upstream checks should_migrate() every iteration "
            "(process_parallel.py:621); noema only at generation boundaries "
            "(controller.py:323). Migration RATE is equivalent, but PHASE differs."
        ),
    )
    def test_migration_fires_on_the_same_iterations(self):
        (_, up_mig), (_, no_mig) = _generation_counters()
        self.assertEqual(up_mig, no_mig)

    def test_divergence_is_the_expected_shape(self):
        """Pins the observed divergence so a future change that alters it is
        visible, and documents that the migration RATE is unchanged."""
        (up_gens, up_mig), (no_gens, no_mig) = _generation_counters()
        # upstream spreads the counter across islands; noema piles it on island 0
        self.assertEqual(up_gens, [3, 3, 3])
        self.assertEqual(no_gens, [3, 0, 0])
        # same number of migrations over the same budget, offset in time
        self.assertEqual(len(up_mig), len(no_mig))
        self.assertEqual(up_mig, [3, 9 - 3])
        self.assertEqual(no_mig, [5, 8])

    def test_noema_refuses_configs_upstream_would_accept(self):
        """noema/substrates/database.py:30-35 hard-rejects novelty settings.
        Upstream's _is_novel short-circuits when embedding_model is None
        (database.py:1069-1071), so the ban is a no-op under default configs —
        but a stock config that DOES set embedding_model cannot be replayed
        through noema at all. That is a study-scope limit, not a silent skew."""
        with self.assertRaises(ValueError):
            IslandsStore(_config(embedding_model="text-embedding-3-small"))
        # novelty_llm alone does not enable novelty upstream, yet noema still bans it
        db = ProgramDatabase(_config(novelty_llm=object()))
        self.assertIsNone(db.embedding_client)
        with self.assertRaises(ValueError):
            IslandsStore(_config(novelty_llm=object()))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
