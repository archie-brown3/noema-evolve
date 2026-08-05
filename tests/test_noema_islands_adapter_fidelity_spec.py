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


def _load_adapter_routed_module(filename: str):
    module_name = f"_adapter_islands_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ProgramDatabase = AdapterProgramDatabase
    return module


def _export_testcases(module) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            globals()[f"AdapterRouted{name}"] = value


for _filename in _DONOR_FILES:
    _export_testcases(_load_adapter_routed_module(_filename))


if __name__ == "__main__":
    unittest.main()
