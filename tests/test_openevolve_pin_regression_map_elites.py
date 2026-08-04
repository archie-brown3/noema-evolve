"""Dependency-pin regression: OpenEvolve MAP-Elites / feature-grid donor tests.

WHAT THIS FILE PROVES
    The pinned ``openevolve`` dependency (pyproject.toml: ``@80945ed``) still
    satisfies its own MAP-Elites test suite (feature coordinate assignment,
    per-cell replacement, feature-stats persistence, grid stability across
    save/load).

WHAT THIS FILE DOES *NOT* PROVE
    Nothing about Noema's fidelity to OpenEvolve -- it does not exercise Noema's
    wrapper at all. See the header of
    ``tests/test_openevolve_pin_regression_islands.py`` for the full explanation
    of why the earlier "routed through NoemaConfig" framing was vacuous.

    In particular this covers OpenEvolve's per-island MAP-Elites grid inside
    ``ProgramDatabase`` -- it is NOT coverage of Noema's separate ``CVTStore``
    (noema/substrates/cvt.py). An earlier commit message wrongly claimed it was.
    CVT substrate fidelity remains an open task 0188 gap.

    THESE MUST NOT BE COUNTED AGAINST ANY TASK 0188 CHECKLIST ITEM.

Donor assertion bodies are the upstream project's own, unmodified.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from openevolve.config import DatabaseConfig
from openevolve.database import ProgramDatabase

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

_MAP_ELITES_FILES = [
    "test_map_elites_features.py",
    "test_feature_stats_persistence.py",
    "test_grid_stability.py",
]


def _pinned_program_database(database_config: DatabaseConfig) -> ProgramDatabase:
    """Construct the pinned upstream ``ProgramDatabase`` the donor tests expect."""
    return ProgramDatabase(database_config)


def _load_pinned_upstream_module(filename: str):
    module_name = f"_pin_map_elites_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ProgramDatabase = _pinned_program_database
    return module


def _export_testcases(module) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            globals()[f"PinnedUpstream{name}"] = value


for _filename in _MAP_ELITES_FILES:
    _export_testcases(_load_pinned_upstream_module(_filename))


if __name__ == "__main__":
    unittest.main()
