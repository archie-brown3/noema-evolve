"""Controller-routed OpenEvolve islands/migration/tracking upstream fidelity (task 0188).

Loads the raw upstream OpenEvolve donor tests (pinned @ 80945ed8, see
tests/upstream/README.md) as plain modules and monkeypatches their
``ProgramDatabase`` binding to route through Noema's own construction path
(``NoemaConfig`` -> ``build_substrate_runtime`` -> ``IslandsStore._db``)
instead of upstream's raw ``openevolve.database.ProgramDatabase(config.database)``
call. The assertion bodies are otherwise byte-for-byte the donor's own —
this proves Noema's config/controller wiring reproduces the exact upstream
database contract, not a reimplementation of it.

Every ``unittest.TestCase`` subclass discovered in each loaded module is
re-exported here so pytest collects it under this file.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from openevolve.database import ProgramDatabase

from noema.config import NoemaConfig, SubstrateConfig
from noema.substrates.registry import build_substrate_runtime

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

_ISLANDS_FILES = [
    "test_island_migration.py",
    "test_island_tracking.py",
    "test_island_child_placement.py",
    "test_island_map_elites.py",
    "test_sample_from_island_ratios.py",
    "test_migration_no_duplicates.py",
    "test_island_parent_consistency.py",
]


def _noema_routed_program_database(database_config):
    """Drop-in replacement for ``openevolve.database.ProgramDatabase(config)``
    that constructs the same class via Noema's substrate-runtime factory."""
    config = NoemaConfig(
        database=database_config,
        substrate=SubstrateConfig(kind="islands"),
    )
    return build_substrate_runtime(config).store._db


def _load_routed_upstream_module(filename: str):
    module_name = f"_upstream_islands_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Route every ProgramDatabase(...) construction in this donor module
    # (setUp and any inline per-test construction) through Noema.
    module.ProgramDatabase = _noema_routed_program_database
    return module


def _export_testcases(module) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            globals()[f"ControllerRouted{name}"] = value


for _filename in _ISLANDS_FILES:
    _export_testcases(_load_routed_upstream_module(_filename))


# Sanity: the routed constructor really does return the upstream class, not a
# lookalike, so `isinstance(self.db, ProgramDatabase)` assertions inside the
# donor tests (if any) still hold.
class _RoutedConstructorReturnsRealProgramDatabase(unittest.TestCase):
    def test_routed_construction_returns_openevolve_program_database(self):
        from openevolve.config import DatabaseConfig

        db = _noema_routed_program_database(DatabaseConfig(in_memory=True, num_islands=2))
        self.assertIsInstance(db, ProgramDatabase)


if __name__ == "__main__":
    unittest.main()
