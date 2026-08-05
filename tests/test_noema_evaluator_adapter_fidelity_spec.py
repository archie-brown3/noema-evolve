"""0188 Stage 3: donor evaluator suite run byte-identical through make_evaluator.

Wrapper-fidelity instrument (canonical method note §2, same shape as Stage 1's
``test_noema_islands_adapter_fidelity_spec.py``): the donor files
``tests/upstream/openevolve/tests/test_evaluator_timeout.py`` (11 tests) and
``test_cascade_validation.py`` (11 tests) are loaded UNMODIFIED and their
module-level ``Evaluator`` name is rebound to ``_RoutedEvaluator``, which
constructs every evaluator through ``noema/evolution/evaluator.py::make_evaluator``.
Donor test bodies resolve the bare name ``Evaluator`` in their module ``__dict__``
at call time, so the rebind routes construction without editing a donor byte.

A failure here is a finding to triage (a: Noema bug / b: widen the wrapper /
c: declared deviation) — never a skip. Triage ledger: vault note
"0188 Stage 3 — Evaluator Routing and Declared Deviations — log".

## Declared deviations: NONE

Stage 3's triage found zero failures: 22/22 donor tests pass routed. That is a
result, not an absence of one — it is the confirmation of the correction in
vault note "0188 OpenEvolve Fidelity Spec §6 — Scope Table — 2026-08-05" §3.2,
which the canonical method note gets wrong. Both of Noema's evaluator
deviations are *default-only*, so neither engages for a caller-supplied config:

1. ``cascade_evaluation=False`` is applied at ``noema/evolution/evaluator.py:29-30``
   ONLY when ``config is None``. Every donor construction supplies its own
   ``EvaluatorConfig``/``Config().evaluator``, so upstream's default
   (``cascade_evaluation: bool = True``, installed pkg ``openevolve/config.py:370``)
   and every explicit donor override pass through untouched — including the nine
   donor tests that deliberately set ``cascade_evaluation=True`` (3 in
   ``test_evaluator_timeout.py``, 6 in ``test_cascade_validation.py``).
2. ``use_llm_feedback`` is rejected outright (``evaluator.py:31-35``). Upstream's
   default is ALREADY ``False`` (installed pkg ``openevolve/config.py:379``), so
   this narrows the reachable config space rather than flipping a default; no
   donor test sets it True, so nothing in this suite reaches the guard.

The remaining construction arguments are identical either way:
``make_evaluator`` builds ``Evaluator(config, evaluation_file, llm_ensemble=None,
prompt_sampler=None, database=None, suffix=".py")`` and upstream's own
``Evaluator.__init__`` defaults those same five to the same values, which is what
both donors pass.

Noema-side pins for the two deviations live where the full suite runs them
(gate 2 ignores this file): ``tests/test_noema_substrate.py::TestMakeEvaluator``
(``config=None`` default path, caller-config pass-through, llm-feedback
rejection) and ``tests/test_noema_controller.py:783`` (``NoemaConfig``'s own
default, a different code path).

## Why the routing guards below are load-bearing

With zero expected failures, a green run is indistinguishable from a rebind that
never took effect — the suite would then re-run upstream against upstream and
call it fidelity. ``TestRoutingActuallyHappens`` closes that: it asserts the
rebind is in place in both donor modules AND that running a donor test really
increments ``_ROUTED_CONSTRUCTIONS``. ``TestDonorSuiteIsFullyCollected`` is
equally load-bearing here: with no triage-ledger parity guard to write (gate 2's
conditional — no rows remain), the count is the only thing standing between a
donor file shrinking and this instrument silently reporting success.
"""

import importlib.util
import io
import unittest
from pathlib import Path
from typing import Optional

from openevolve.config import EvaluatorConfig

from noema.evolution.evaluator import make_evaluator

_UPSTREAM_TESTS = Path(__file__).parent / "upstream" / "openevolve" / "tests"

_DONOR_FILES = ["test_evaluator_timeout.py", "test_cascade_validation.py"]

# Verified donor collection counts: 11 timeout + 11 cascade-validation.
_EXPECTED_DONOR_TESTS = 22

# Routing odometer — see TestRoutingActuallyHappens.
_ROUTED_CONSTRUCTIONS = 0


def _RoutedEvaluator(
    config: EvaluatorConfig,
    evaluation_file: str,
    llm_ensemble=None,
    prompt_sampler=None,
    database=None,
    suffix: Optional[str] = ".py",
):
    """Stands in for ``openevolve.evaluator.Evaluator`` in the donor modules.

    Strict, in the Stage 1 sense: ``make_evaluator`` hardcodes ``llm_ensemble``,
    ``prompt_sampler`` and ``database`` to ``None``, so a donor passing anything
    else must raise naming the narrowed capability rather than have its argument
    silently absorbed. (Dead in this suite — both donors pass ``None`` — but an
    absorbed argument would resurface later as a misleading assertion mismatch,
    which the triage method reads as an (a) real-Noema-bug signal.)
    """
    global _ROUTED_CONSTRUCTIONS
    for name, value in (
        ("llm_ensemble", llm_ensemble),
        ("prompt_sampler", prompt_sampler),
        ("database", database),
    ):
        if value is not None:
            raise NotImplementedError(
                f"missing wrapper capability: make_evaluator hardcodes {name}=None "
                "(noema/evolution/evaluator.py:36-43); it exposes no way to pass one"
            )
    _ROUTED_CONSTRUCTIONS += 1
    return make_evaluator(config, evaluation_file, suffix=suffix)


def _load_routed_module(filename: str):
    module_name = f"_routed_evaluator_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(module_name, _UPSTREAM_TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.Evaluator = _RoutedEvaluator
    return module


def _export_testcases(module, stem: str) -> None:
    for name, value in vars(module).items():
        if (
            isinstance(value, type)
            and issubclass(value, unittest.TestCase)
            and value is not unittest.TestCase
        ):
            # Keyed by donor file stem as well as class name: two donor files
            # sharing a class name would otherwise shadow one out of collection,
            # an accidental skip the no-skip triage method forbids.
            globals()[f"AdapterRouted_{stem}_{name}"] = value


_ROUTED_MODULES = {f[:-3]: _load_routed_module(f) for f in _DONOR_FILES}

for _stem, _module in _ROUTED_MODULES.items():
    _export_testcases(_module, _stem)


class TestRoutingActuallyHappens(unittest.TestCase):
    """Proves the donor suite is routed, not merely green."""

    def test_donor_modules_have_the_rebound_evaluator(self):
        for stem, module in _ROUTED_MODULES.items():
            self.assertIs(module.Evaluator, _RoutedEvaluator, stem)

    def test_running_a_donor_test_constructs_through_make_evaluator(self):
        # End-to-end: a donor body resolving the bare name `Evaluator` at call
        # time must land in _RoutedEvaluator. Uses a donor test with no sleeps.
        case = _ROUTED_MODULES["test_cascade_validation"].TestCascadeValidation(
            "test_no_cascade_validation_when_disabled"
        )
        before = _ROUTED_CONSTRUCTIONS
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(case)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertGreater(
            _ROUTED_CONSTRUCTIONS,
            before,
            "donor construction did not route through make_evaluator — the "
            "module-level Evaluator rebind is not reaching the donor test body",
        )


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
            "must appear in the Stage 3 triage ledger",
        )


if __name__ == "__main__":
    unittest.main()
