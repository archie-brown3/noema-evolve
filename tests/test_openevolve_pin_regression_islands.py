"""Dependency-pin regression: OpenEvolve islands/migration/tracking donor tests.

WHAT THIS FILE PROVES
    The pinned ``openevolve`` dependency (pyproject.toml: ``@80945ed``) still
    satisfies its own island/migration/tracking test suite. If the pin moves and
    upstream's behavior drifts, these fire.

WHAT THIS FILE DOES *NOT* PROVE
    Nothing about Noema's fidelity to OpenEvolve. It does not exercise Noema's
    wrapper. An earlier version of this file constructed the database via
    ``NoemaConfig`` -> ``build_substrate_runtime`` -> ``IslandsStore._db`` and
    claimed that proved Noema's wiring reproduced the upstream contract. It did
    not: ``NoemaConfig`` stores the donor's ``DatabaseConfig`` by object identity
    and ``SubstrateDatabase.__init__`` (noema/substrates/database.py) forwards it
    verbatim to ``ProgramDatabase``, so that routing was an identity function.
    Demonstrated: replacing every ``IslandsStore`` method with one that raises
    left all of these tests passing, and an execution trace showed they reach
    only constructors in ``noema/``.

    THESE MUST NOT BE COUNTED AGAINST ANY TASK 0188 CHECKLIST ITEM.

    Genuine islands fidelity coverage lives in
    ``tests/test_noema_islands_wrapper_fidelity_spec.py`` (exercises the wrapper's
    public API) and ``tests/test_noema_islands_fidelity_spec.py`` (seeded RNG-trace
    equality against a direct ``ProgramDatabase``).

The donor assertion bodies are the upstream project's own, unmodified; every
``unittest.TestCase`` discovered in each loaded module is re-exported so pytest
collects it here.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from openevolve.config import DatabaseConfig
from openevolve.database import ProgramDatabase

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

_ISLANDS_FILES = [
    "test_island_migration.py",
    "test_island_tracking.py",
    "test_island_child_placement.py",
    "test_island_map_elites.py",
    "test_sample_from_island_ratios.py",
    "test_migration_no_duplicates.py",
    "test_island_parent_consistency.py",
    # Added by 0188 Stage 1 triage: every (c) declared deviation must leave the
    # DONOR's own claim verified against the donor. These two files carry the
    # `current_island` / `set_current_island` / instance-monkeypatch claims that
    # Noema's wrapper deliberately does not expose, and were the only two donor
    # files in the adapter suite with no pin coverage at all.
    "test_concurrent_island_access.py",
    "test_island_isolation.py",
]

# Collected-item count for _ISLANDS_FILES, pytest-verified. Guarded below:
# test_island_isolation.py and test_island_migration.py BOTH define a class
# named `TestIslandMigration`, so a name-only re-export key silently drops one
# file's tests from collection.
_EXPECTED_PINNED_TESTS = 68


def _pinned_program_database(database_config: DatabaseConfig) -> ProgramDatabase:
    """Construct the pinned upstream ``ProgramDatabase`` the donor tests expect."""
    return ProgramDatabase(database_config)


def _load_pinned_upstream_module(filename: str):
    module_name = f"_pin_islands_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ProgramDatabase = _pinned_program_database
    return module


def _export_testcases(module, stem: str) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            globals()[f"PinnedUpstream_{stem}_{name}"] = value


for _filename in _ISLANDS_FILES:
    _export_testcases(_load_pinned_upstream_module(_filename), _filename[:-3])


class TestPinnedDonorSuiteIsFullyCollected(unittest.TestCase):
    """Not a donor test: proves no donor test vanished during re-export."""

    def test_every_pinned_donor_test_is_collected(self):
        exported = [
            value for name, value in globals().items() if name.startswith("PinnedUpstream_")
        ]
        collected = sum(
            len(unittest.defaultTestLoader.getTestCaseNames(cls)) for cls in exported
        )
        self.assertEqual(collected, _EXPECTED_PINNED_TESTS)


if __name__ == "__main__":
    unittest.main()
