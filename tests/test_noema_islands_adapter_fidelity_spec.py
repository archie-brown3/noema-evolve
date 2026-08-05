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

## Declared deviations: NONE (0188 Stage 7)

Stage 1 classified all 51 then-failing donor tests as (c) DECLARED DEVIATION,
on the standing evidence that no Noema production caller needed the missing
capabilities. Archie's 2026-08-05 redirect reversed that call by authority:
the whole donor suite becomes a permanent fidelity gate, so the wrapper was
widened until every donor call had a Noema capability to route to (vault note
"0188 Stage 7 -- Wrapper Widening -- log"). ``DECLARED_DEVIATIONS`` is now
EMPTY and ``TestTriageLedgerParity`` asserts the donor suite fails nowhere.

That inverts what this file guards. It no longer certifies a curated set of
known gaps; it certifies that 90 unmodified OpenEvolve tests get upstream's own
answers back out of Noema's wrapper. A failure here is a finding, in the
method's usual order: (a) a real Noema bug if a donor assertion now disagrees
with a Noema-routed answer, (b) a still-missing capability if a call raises
``NotImplementedError``. There is no (c) column left to reach for.

What the widened capabilities are NOT: reimplementations. Each one hands back
the state ``ProgramDatabase`` already owns (see ``noema/substrates/database.py``,
"upstream state, served live"), so upstream behaviour -- including its quirks,
such as ``island_best_programs`` holding a stale id after a deletion -- is
reproduced structurally rather than asserted. The 11 registered defects in
"0188 Known Defects Register -- 2026-08-05" stay parked for exactly this reason:
a widened capability that "fixed" one of them would be diverging from the pin,
not converging on it.
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


# 0188 Stage 7 finding — RUN THIS FILE WITH ``PYTHONHASHSEED=0``.
#
# ``test_sample_from_island_ratios.py:186`` (test_weighted_mode_favors_high_fitness)
# fails on its own terms roughly 1 run in 25, and Stage 1 recorded it as "a
# stochastic donor with no seed". That diagnosis was wrong, and seeding does not
# fix it: the donor seeds `random` itself (`random.seed(42)`, :190). The actual
# lever is PYTHONHASHSEED. Upstream's `_sample_from_island_weighted`
# (openevolve/database.py:1438) does `list(self.islands[island_id])` on a SET, so
# which program each fixed RNG draw lands on depends on string-hash
# randomisation — a fresh roll of the experiment every process. Measured, same
# `random.seed(42)`, 200 draws, mean 0.6100:
#
#     PYTHONHASHSEED=0    -> sampled 0.6117, bit-identical across processes
#     PYTHONHASHSEED=rand -> sampled 0.6100 .. 0.6165 (0.6100 is a FAIL: the
#                            donor asserts strictly greater-than)
#
# The donor's effect size is ~1.3 standard errors at n=200, so the assertion is
# simply under-powered — an upstream defect, reproduced identically against the
# REAL pinned ProgramDatabase in test_openevolve_pin_regression_islands.py. It is
# not a Noema finding and it must not be "fixed" Noema-side: the donor files stay
# byte-identical, and the order comes from a set upstream owns and the wrapper
# never sees.
#
# Fixing the hash seed is therefore the only lever that does not diverge from the
# pin, and it cannot be set from inside a running interpreter — hence it belongs
# to the gate command. Recorded as a dated gate adjustment in the Stage 7 note.


# ---------------------------------------------------------------------------
# Triage ledger — machine-checked half
# ---------------------------------------------------------------------------
# EMPTY as of 0188 Stage 7. Every donor test in this file passes through the
# wrapper; nothing is declared away. The full rows (what each of the 51 former
# deviations was, and which wrapper capability now serves it) live in vault
# note "0188 Stage 7 — Wrapper Widening — log".
#
# `tests/test_adapter_instrumentation.py::TestTriageLedgerParity` asserts the
# observed failing set is EXACTLY this table — so with the table empty, ANY
# donor failure fires it. That is the point: a capability cannot silently
# regress into a "known gap" again without an explicit row being added here and
# justified in the stage log.
DECLARED_DEVIATIONS = {}


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
