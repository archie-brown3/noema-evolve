"""Import-home regression tests for Noema's semantic package layout."""

from importlib import import_module
from importlib.machinery import PathFinder
from pathlib import Path

import noema


SEMANTIC_MODULES = (
    "noema.substrates.base",
    "noema.substrates.database",
    "noema.substrates.registry",
    "noema.substrates.islands",
    "noema.substrates.tree",
    "noema.substrates.cvt",
    "noema.substrates.cvt_behavior",
    "noema.evolution.boundary",
    "noema.evolution.diff",
    "noema.evolution.evaluator",
    "noema.evolution.operators",
    "noema.evolution.prompts",
    "noema.evolution.views",
)

OBSOLETE_MODULES = (
    "noema.base",
    "noema.database",
    "noema.registry",
    "noema.islands",
    "noema.tree",
    "noema.cvt",
    "noema.cvt_behavior",
    "noema.boundary",
    "noema.diff",
    "noema.evaluator",
    "noema.operators",
    "noema.prompts",
    "noema.views",
)


def test_semantic_modules_are_importable():
    for name in SEMANTIC_MODULES:
        import_module(name)


def test_flat_modules_are_absent_from_the_installed_package():
    package_directory = Path(noema.__file__).resolve().parent
    for name in OBSOLETE_MODULES:
        assert PathFinder.find_spec(name, [str(package_directory)]) is None
