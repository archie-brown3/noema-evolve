"""Controller-routed OpenEvolve prompt-sampler upstream fidelity (task 0188).

test_prompt_sampler.py and test_prompt_sampler_comprehensive.py construct
``openevolve.prompt.sampler.PromptSampler`` directly; both are routed through
Noema's own ``noema.evolution.prompts.make_prompt_sampler`` factory instead
(same monkeypatch-the-donor's-constructor-binding technique used for the
islands/database/MAP-Elites clusters). ``make_prompt_sampler`` returns the
literal upstream ``PromptSampler`` class with template stochasticity forced
off and Noema's EoH-derived operator templates additionally registered —
neither test file asserts on template_manager internals, so this is
behavior-preserving for the donor assertions.

Upstream's ``Config()`` defaults ``use_template_stochasticity=True``;
``make_prompt_sampler`` raises on that (a declared, intentional Noema
deviation — see noema/evolution/prompts.py — because random phrase
variation would void the identical-prompts-across-arms guarantee). The
routed constructor forces it off before construction, exactly as every real
Noema config path already does; no donor test in this cluster asserts on
stochastic phrasing, so this is the one declared exemption, not a
behavior change to what's being tested.

test_changes_description.py constructs no PromptSampler at all (it only
exercises PromptConfig validation), so it needs no routing and is imported
verbatim.
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


def _noema_routed_prompt_sampler(prompt_config):
    prompt_config.use_template_stochasticity = False
    return make_prompt_sampler(prompt_config)


def _load_module(filename: str, *, route: bool):
    module_name = f"_upstream_prompt_sampler_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if route:
        module.PromptSampler = _noema_routed_prompt_sampler
    return module


def _export_testcases(module) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            globals()[f"ControllerRouted{name}"] = value


for _filename in _ROUTED_FILES:
    _export_testcases(_load_module(_filename, route=True))
for _filename in _UNROUTED_FILES:
    _export_testcases(_load_module(_filename, route=False))


if __name__ == "__main__":
    unittest.main()
