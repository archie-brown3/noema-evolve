"""Controller-routed OpenEvolve MAP-Elites/feature-grid upstream fidelity (task 0188).

Same routing technique as test_noema_islands_upstream_fidelity_spec.py: each
donor module's ``ProgramDatabase`` binding is monkeypatched to construct via
Noema's ``NoemaConfig`` -> ``build_substrate_runtime`` path, so every
``ProgramDatabase(config)`` call site (setUp and inline ``db1``/``db2``/``db3``
constructions) is routed through Noema without touching the donor's own
assertion bodies.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from noema.config import NoemaConfig, SubstrateConfig
from noema.substrates.registry import build_substrate_runtime

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

_MAP_ELITES_FILES = [
    "test_map_elites_features.py",
    "test_feature_stats_persistence.py",
    "test_grid_stability.py",
]


def _noema_routed_program_database(database_config):
    config = NoemaConfig(
        database=database_config,
        substrate=SubstrateConfig(kind="islands"),
    )
    return build_substrate_runtime(config).store._db


def _load_routed_upstream_module(filename: str):
    module_name = f"_upstream_map_elites_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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


for _filename in _MAP_ELITES_FILES:
    _export_testcases(_load_routed_upstream_module(_filename))


if __name__ == "__main__":
    unittest.main()
