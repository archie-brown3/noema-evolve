"""0188 Stage 1: donor island suite run byte-identical through the strict adapter.

Wrapper-fidelity instrument (canonical method note §2): OpenEvolve ships a
runnable test suite, so the donor files under ``tests/upstream/openevolve/tests``
are loaded UNMODIFIED and their module-level ``ProgramDatabase`` name is rebound
to ``AdapterProgramDatabase``, which serves every call from ``IslandsStore``.
A failure here is a finding to triage (a: Noema bug / b: widen wrapper /
c: declared deviation) — never a skip. Triage ledger: vault note
"0188 Stage 1 — Adapter, Instrumentation, Triage — log".

Mechanism reuses the proven post-exec rebind from
``test_openevolve_pin_regression_islands.py`` (donor test bodies resolve the
bare name ``ProgramDatabase`` in their module __dict__ at call time). The
donor files themselves are never edited.

## Declared deviations

Stage 1's triage classified all 51 failures below as (c) DECLARED DEVIATION --
zero (a) real-Noema-bug findings, zero (b) widen-the-wrapper findings. The
per-test table is ``DECLARED_DEVIATIONS`` further down; this is the standing
justification behind it.

Noema's ``IslandsStore``/``SubstrateDatabase`` is a NARROW wrapper, not a
re-export of ``ProgramDatabase``. Every failure below is a donor test reaching
for a capability the wrapper deliberately does not expose, in one of four
shapes:

1. **Private upstream internals** -- ``_calculate_complexity_bin``,
   ``_calculate_diversity_bin``, ``_calculate_feature_coords``,
   ``_fast_code_diversity``, ``_sample_inspirations``. These are claims about
   *the donor's own implementation*, so the correct instrument is the donor
   itself: ``tests/test_openevolve_pin_regression_database.py`` and
   ``tests/test_openevolve_pin_regression_islands.py`` run these exact bodies
   against the REAL pinned ``ProgramDatabase``. Exposing binning internals
   through Noema's wrapper would be new public surface no Noema code path calls.

2. **Internal bookkeeping containers** -- ``island_generations``,
   ``island_feature_maps``, ``archive``, ``current_island``/``next_island``,
   ``best_program_id`` (write), ``should_migrate``. Same pin coverage as (1) for
   the donor's claim. The *semantics* Noema actually needs are pinned
   wrapper-side, through the public API, in
   ``tests/test_noema_islands_wrapper_fidelity_spec.py``:
   ``TestIslandGenerationBookkeeping`` (:133) for generation/migration cadence,
   ``TestIslandMapElitesThroughWrapper`` (:785) for per-island MAP-Elites
   placement and cell replacement, ``TestIslandMigrationThroughWrapper`` (:629)
   for migration behaviour.
   Evidence that (b) is wrong here: ZERO production callers reach around the
   wrapper (§4 dossier §1(i)/(iii) -- ``grep '\\._db\\b' noema/`` excluding
   ``noema/substrates/`` returns nothing, and the only Noema reader of upstream
   island state, ``noema/export_evoreplay.py:137,346``, reads checkpoint JSON).

3. **Direct mutation of internal containers** -- ``del db.programs[id]``,
   ``db.islands[i].remove(id)``, ``db.current_island = i``,
   ``db.island_generations = [...]``, ``db.best_program_id = "x"``, and
   monkeypatching ``db.sample_from_island`` on the live instance. The wrapper
   exposes no mutation path for any of these; ``add()``/``end_generation()``
   are the only write paths. The adapter serves ``programs``/``islands`` as
   derived READ-ONLY views precisely so these raise at the mutation instead of
   being absorbed by a throwaway copy (an absorbed mutation resurfaces later as
   an assertion mismatch, which this method reads as an (a) signal).

4. **Signature surface Noema narrowed on purpose** -- ``sample()`` with no
   island (Noema always targets an island explicitly) and
   ``get_top_programs(metric=...)`` (Noema ranks by the single fixed fitness
   convention, ``get_fitness_score``).

Two donor files carried claims with NO pin coverage before this stage --
``test_concurrent_island_access.py`` and ``test_island_isolation.py``. Stage 1
added both to ``tests/test_openevolve_pin_regression_islands.py`` so every (c)
row's donor claim is verified against the donor.
``test_concurrent_island_access.py`` is pinned with an explicit disclaimer: its
own docstring frames it as a reproduction of GitHub issue #246, a documented
upstream RACE, not a behavioural contract Noema owes. Noema's answer to that
race is structural, not a reproduction -- island targeting is a per-call
argument, never shared mutable state -- and that is asserted directly by
``tests/test_noema_islands_wrapper_fidelity_spec.py::
TestIslandConcurrencyThroughWrapper``.
"""

import importlib.util
import unittest
from pathlib import Path

from tests.adapter_islands_store import AdapterProgramDatabase

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

# Spec §3 §2 target suite: the 8-file island cluster (58 tests) +
# test_sample_from_island_ratios.py (10, classified IN) + test_database.py
# (22, admitted with the white-box caveat — its private-method subset is
# expected to fail loud and be triaged, not skipped).
_DONOR_FILES = [
    "test_concurrent_island_access.py",
    "test_island_child_placement.py",
    "test_island_isolation.py",
    "test_island_map_elites.py",
    "test_island_migration.py",
    "test_island_parent_consistency.py",
    "test_island_tracking.py",
    "test_migration_no_duplicates.py",
    "test_sample_from_island_ratios.py",
    "test_database.py",
]


# spec §3 §2 verified collection counts: 58 island cluster + 10 ratios + 22 database.
_EXPECTED_DONOR_TESTS = 90


def _load_adapter_routed_module(filename: str):
    module_name = f"_adapter_islands_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ProgramDatabase = AdapterProgramDatabase
    return module


def _export_testcases(module, stem: str) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            # Key by DONOR FILE STEM, not class name alone: test_island_isolation.py
            # and test_island_migration.py both define `TestIslandMigration`, so a
            # name-only key silently shadows one out of collection — an accidental
            # skip, which the no-skip triage method forbids. Guarded below.
            globals()[f"AdapterRouted_{stem}_{name}"] = value


for _filename in _DONOR_FILES:
    _export_testcases(_load_adapter_routed_module(_filename), _filename[:-3])


# ---------------------------------------------------------------------------
# Triage ledger — machine-checked half
# ---------------------------------------------------------------------------
# Every donor test below is classification (c), DECLARED DEVIATION. The full
# rows (classification, evidence, action) live in vault note
# "0188 Stage 1 — Adapter, Instrumentation, Triage — log"; the justification
# per capability group is in "## Declared deviations" below. The value is the
# capability token the failure MUST name.
#
# Why (c) and not (b) "widen the wrapper", for every one of them: the six
# unexposed internals have ZERO production consumers outside
# `noema/substrates/` (§4 dossier §1(iii): the only Noema reader of upstream
# island state is `noema/export_evoreplay.py:137,346`, and it reads checkpoint
# JSON, not a live store). Widening `IslandsStore` to satisfy a donor test
# would add public surface no Noema code path calls.
#
# Why (c) and not (a) "real Noema bug": every entry raises a NAMED missing
# capability before reaching any assertion about Noema's behaviour. There is
# no assertion mismatch on the servable surface anywhere in this suite — see
# the Stage 1 note's "(a) findings" section.
#
# `tests/test_adapter_instrumentation.py::TestTriageLedgerParity` asserts the
# observed failing set is EXACTLY this table, so no row can rot: if Noema ever
# grows one of these capabilities the donor test starts passing and the guard
# fires; if a donor test starts failing for a new reason, the guard fires too.
DECLARED_DEVIATIONS = {
    # -- private upstream internals (binning / diversity / inspiration sampling)
    # Donor claim is about ProgramDatabase's own internals; pin-covered in
    # test_openevolve_pin_regression_database.py / _islands.py.
    "test_database.TestProgramDatabase.test_calculate_complexity_bin_adaptive": "ProgramDatabase._calculate_complexity_bin",
    "test_database.TestProgramDatabase.test_calculate_complexity_bin_cold_start": "ProgramDatabase._calculate_complexity_bin",
    "test_database.TestProgramDatabase.test_calculate_diversity_bin_adaptive": "ProgramDatabase._fast_code_diversity",
    "test_database.TestProgramDatabase.test_calculate_diversity_bin_cold_start": "ProgramDatabase._calculate_diversity_bin",
    "test_database.TestProgramDatabase.test_calculate_diversity_bin_identical_programs": "ProgramDatabase._calculate_diversity_bin",
    "test_database.TestProgramDatabase.test_diversity_feature_integration": "ProgramDatabase._calculate_feature_coords",
    "test_database.TestProgramDatabase.test_fast_code_diversity_function": "ProgramDatabase._fast_code_diversity",
    "test_database.TestProgramDatabase.test_feature_coordinates_calculation": "ProgramDatabase._calculate_feature_coords",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_migration_uses_map_elites_deduplication": "ProgramDatabase._calculate_feature_coords",
    "test_island_tracking.TestIslandTracking.test_sample_inspirations_from_island": "ProgramDatabase._sample_inspirations",
    # -- island_generations: the donor's white-box read/write of the per-island
    # generation counter list. Noema's equivalent semantics are pinned
    # wrapper-side by TestIslandGenerationBookkeeping
    # (tests/test_noema_islands_wrapper_fidelity_spec.py:133).
    "test_database.TestProgramDatabase.test_migration_prevents_re_migration": "ProgramDatabase.island_generations",
    "test_database.TestProgramDatabase.test_migration_validation_passes": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_initial_island_setup": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_migration_creates_proper_copies": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_migration_preserves_best_programs": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_migration_rate_respected": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_migration_ring_topology": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_migration_updates_generations": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_migration_with_empty_islands": "ProgramDatabase.island_generations",
    "test_island_migration.TestIslandMigration.test_no_migration_with_single_island": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_migrated_program_content_preserved": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_migration_creates_clean_uuid_ids": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_migration_skips_duplicate_code_on_target_island": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_migration_target_islands_are_different": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_migration_with_feature_map_conflicts_resolved_cleanly": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_multiple_migration_rounds_no_exponential_growth": "ProgramDatabase.island_generations",
    "test_migration_no_duplicates.TestMigrationNoDuplicates.test_no_duplicate_program_ids_across_all_islands": "ProgramDatabase.island_generations",
    # -- should_migrate / migrate_programs as standalone calls: Noema drives
    # both from inside end_generation() (noema/substrates/database.py:104-123).
    "test_island_migration.TestIslandMigration.test_should_migrate_logic": "ProgramDatabase.should_migrate",
    # -- island_feature_maps: the donor asserts over the raw MAP-Elites cell
    # dicts. Wrapper-side equivalent: TestIslandMapElitesThroughWrapper
    # (tests/test_noema_islands_wrapper_fidelity_spec.py:785).
    "test_database.TestProgramDatabase.test_feature_map_operations": "ProgramDatabase.island_feature_maps",
    "test_island_map_elites.TestIslandMapElites.test_better_program_replaces_in_island_feature_map": "ProgramDatabase.island_feature_maps",
    "test_island_map_elites.TestIslandMapElites.test_checkpoint_serialization_preserves_island_maps": "ProgramDatabase.island_feature_maps",
    "test_island_map_elites.TestIslandMapElites.test_feature_coordinate_isolation": "ProgramDatabase.island_feature_maps",
    "test_island_map_elites.TestIslandMapElites.test_island_feature_maps_initialization": "ProgramDatabase.island_feature_maps",
    "test_island_map_elites.TestIslandMapElites.test_no_migrant_suffix_generation": "ProgramDatabase.island_feature_maps",
    "test_island_map_elites.TestIslandMapElites.test_program_added_to_correct_island_feature_map": "ProgramDatabase.island_feature_maps",
    # -- current_island / next_island: upstream production NEVER advances
    # current_island (§4 dossier §1(ii)); Noema targets islands per call
    # instead of via shared mutable state.
    "test_island_parent_consistency.TestIslandParentConsistency.test_multiple_generations_island_drift": "ProgramDatabase.next_island",
    "test_island_parent_consistency.TestIslandParentConsistency.test_parent_child_island_consistency": "ProgramDatabase.next_island",
    # -- archive: exploitation-mode fallback container, never referenced by
    # SubstrateDatabase/IslandsStore.
    "test_database.TestProgramDatabase.test_archive_operations": "ProgramDatabase.archive",
    "test_sample_from_island_ratios.TestSampleFromIslandEdgeCases.test_empty_archive_fallback": "ProgramDatabase.archive",
    # -- log_island_status: upstream's per-island status logger. Donor claim is
    # only "does not raise"; Noema logs through its own logger.
    "test_island_tracking.TestIslandTracking.test_island_status_logging": "ProgramDatabase.log_island_status",
    # -- bare sample(): reads the donor's mutable current_island. Noema only
    # ever samples with an explicit island (sample_from_island/native_select).
    "test_database.TestProgramDatabase.test_sample": "ProgramDatabase.sample",
    # -- get_top_programs(metric=...): per-metric ranking. Noema ranks by the
    # single fixed fitness convention (get_fitness_score).
    "test_database.TestProgramDatabase.test_get_top_programs_with_metrics": "get_top_programs(metric=...)",
    # -- direct writes to best_program_id: no setter on the wrapper; the
    # adapter serves it as a derived read.
    "test_database.TestProgramDatabase.test_empty_island_initialization_creates_copies": "ProgramDatabase.best_program_id",
    "test_database.TestProgramDatabase.test_no_program_assigned_to_multiple_islands": "ProgramDatabase.best_program_id",
    # -- direct mutation of the programs dict / island membership sets. The
    # adapter serves these as DERIVED READ-ONLY views, so a donor mutation
    # raises here instead of being absorbed by a throwaway copy.
    "test_island_isolation.TestIslandIsolation.test_database_current_island_restoration": "ProgramDatabase.islands[i]",
    "test_island_isolation.TestIslandIsolation.test_island_distribution_in_batch": "ProgramDatabase.islands[i]",
    "test_island_isolation.TestIslandIsolation.test_submit_iteration_uses_correct_island": "ProgramDatabase.islands[i]",
    "test_island_isolation.TestIslandMigration.test_migration_preserves_island_structure": "ProgramDatabase.programs",
    "test_island_tracking.TestIslandTracking.test_island_best_with_missing_program": "ProgramDatabase.programs",
    # -- monkeypatching sample_from_island on the live instance (donor's own
    # thread-safety harness for GitHub issue #246).
    "test_concurrent_island_access.TestConcurrentIslandAccess.test_proposed_fix_with_island_specific_sampling": "ProgramDatabase.sample_from_island",
    "test_island_isolation.TestIslandIsolation.test_island_isolation_during_evolution": "ProgramDatabase.sample_from_island",
}


def run_donor_suite():
    """Run every adapter-routed donor test; return {test_key: failure text}.

    Used by ``tests/test_adapter_instrumentation.py`` to hold the ledger to the
    observed reality. Keys match ``DECLARED_DEVIATIONS``.
    """
    import io

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for name, value in globals().items():
        if name.startswith("AdapterRouted_"):
            suite.addTests(loader.loadTestsFromTestCase(value))
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    return {
        test.id().replace("_adapter_islands_", "", 1): text
        for test, text in result.failures + result.errors
    }


class TestDonorSuiteIsFullyCollected(unittest.TestCase):
    """Not a donor test: proves no donor test vanished during re-export."""

    def test_every_donor_test_is_collected(self):
        exported = [
            value for name, value in globals().items() if name.startswith("AdapterRouted_")
        ]
        collected = sum(
            len(unittest.defaultTestLoader.getTestCaseNames(cls)) for cls in exported
        )
        self.assertEqual(
            collected,
            _EXPECTED_DONOR_TESTS,
            f"{collected} donor tests collected, expected {_EXPECTED_DONOR_TESTS} — "
            "a donor test was shadowed or a donor file changed; every donor test "
            "must appear in the Stage 1 triage ledger",
        )


if __name__ == "__main__":
    unittest.main()
