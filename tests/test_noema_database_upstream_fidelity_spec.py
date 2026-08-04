"""Controller-routed OpenEvolve ProgramDatabase core upstream fidelity (task 0188).

Same routing technique as test_noema_islands_upstream_fidelity_spec.py: the
donor module's ``ProgramDatabase`` binding is monkeypatched to construct via
Noema's ``NoemaConfig`` -> ``build_substrate_runtime`` path, so every
``ProgramDatabase(config.database)`` call site in the donor file (setUp and
the several inline ``multi_db`` constructions) is routed through Noema
without touching the donor's own assertion bodies.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from noema.config import NoemaConfig, SubstrateConfig
from noema.substrates.registry import build_substrate_runtime

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"


def _noema_routed_program_database(database_config):
    config = NoemaConfig(
        database=database_config,
        substrate=SubstrateConfig(kind="islands"),
    )
    return build_substrate_runtime(config).store._db


def _load_routed_upstream_module(filename: str):
    module_name = f"_upstream_database_{filename[:-3]}"
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


_export_testcases(_load_routed_upstream_module("test_database.py"))


if __name__ == "__main__":
    unittest.main()
