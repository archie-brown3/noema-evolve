"""Dependency-pin regression: OpenEvolve prompt-sampler donor tests.

WHAT THIS FILE PROVES
    1. The pinned ``openevolve`` dependency (pyproject.toml: ``@80945ed``) still
       satisfies its own ``PromptSampler`` test suite.
    2. Narrowly, and unlike its three sibling pin-regression files, it DOES
       exercise one piece of Noema: ``noema.evolution.prompts.make_prompt_sampler``
       genuinely runs here, so these tests would catch it if Noema's EoH operator
       template registration broke upstream prompt building.

WHAT THIS FILE DOES *NOT* PROVE
    Anything about Noema's own prompt seam. ``build_mutation_prompt`` and
    ``inject_advice`` (noema/evolution/prompts.py) -- where the shared-prefix and
    coordination-block guarantees actually live -- are never called.

    A caveat on (2): upstream's ``Config()`` defaults
    ``use_template_stochasticity=True`` and ``make_prompt_sampler`` raises on that
    (a declared Noema deviation -- random phrase variation would void the
    identical-prompts-across-arms guarantee). ``_pinned_prompt_sampler`` forces it
    off before construction, so these donor tests no longer run under upstream's
    default config. No assertion in this cluster depends on stochastic phrasing.

    THIS MUST NOT BE COUNTED AGAINST ANY TASK 0188 CHECKLIST ITEM. Prompt-stack
    fidelity is covered by ``tests/test_noema_prompts.py``.

``test_changes_description.py`` constructs no ``PromptSampler`` -- it only
exercises ``PromptConfig`` validation -- so it is loaded unrouted.
Donor assertion bodies are the upstream project's own, unmodified.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from noema.evolution.prompts import make_prompt_sampler

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

_ROUTED_FILES = [
    "test_prompt_sampler.py",
    "test_prompt_sampler_comprehensive.py",
]
_UNROUTED_FILES = [
    "test_changes_description.py",
]


def _pinned_prompt_sampler(prompt_config):
    """Build the sampler via Noema's factory, with the declared stochasticity
    deviation applied (see module docstring)."""
    prompt_config.use_template_stochasticity = False
    return make_prompt_sampler(prompt_config)


def _load_module(filename: str, *, route: bool):
    module_name = f"_pin_prompt_sampler_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if route:
        module.PromptSampler = _pinned_prompt_sampler
    return module


def _export_testcases(module) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            globals()[f"PinnedUpstream{name}"] = value


for _filename in _ROUTED_FILES:
    _export_testcases(_load_module(_filename, route=True))
for _filename in _UNROUTED_FILES:
    _export_testcases(_load_module(_filename, route=False))


if __name__ == "__main__":
    unittest.main()
