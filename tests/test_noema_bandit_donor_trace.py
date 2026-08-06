"""Kernel fidelity for the bandit arm: seeded differential traces vs ShinkaEvolve.

Task 0188 Stage 8.  Noema REIMPLEMENTED ShinkaEvolve's ``AsymmetricUCB``
(~105 stdlib lines against the donor's 1,470 numpy lines), so there is no
wrapper to adapt and the donor's own ``test_bandit_persistence.py`` cannot run
against it — that file pins ``posterior``/``select_llm``/``save_state`` and,
in 12 of its 22 tests, an arm-set RESIZE contract that Noema's
``load_state_dict`` deliberately rejects.  The instrument that does work is a
seeded differential trace of the scoring kernel.

Two tiers (settled 2026-08-05):

* **Tier 1 — always runs, donor not required.**  ``tests/fixtures/bandit/
  donor_traces.json`` holds traces AUTHORED BY THE DONOR: at each step the
  donor chose the arm (argmax of its posterior) and the donor's resulting
  state was recorded.  Noema must reproduce the choice and the state.  The
  goldens are donor output, not a transcript of Noema's own behaviour, so
  replaying them is not circular.
* **Tier 2 — needs the pinned clone.**  Regenerates from the live donor class
  and asserts the committed goldens still describe it.  When the clone is
  absent the tier skips **with the reason and the path in the message**, never
  silently; ``test_tier_two_skip_is_loud`` proves that.

Donor bytes never enter this repo (external-clone rule); only the numeric
traces above, which are output, not source.

**Neutralizing config** — the donor construction under which its scoring path
reduces to Noema's.  ``posterior()`` then computes
``_normalized_means + c*sqrt(2*ln(max(t,2))/n)`` (prioritization.py:622-627),
the same expression as ``module.py:115``, and ``epsilon=0`` collapses it to a
point mass on the argmax (prioritization.py:642-651)::

    AsymmetricUCB(arm_names=..., exploration_coef=c, epsilon=0.0,
                  exponential_base=None,   # linear, not log-space (:349)
                  auto_decay=None,         # no decay (:133-136)
                  cost_aware_coef=0.0,     # cost-blind (:629-641)
                  shift_by_baseline=False, shift_by_parent=True,
                  adaptive_scale=True, asymmetric_scaling=True)

Every OTHER difference is a ledgered deviation (``module.py:44-52``): cost
machinery dropped, exponential/posterior-sampling scaling dropped,
epsilon-greedy dropped, numpy replaced by the stdlib.

**Discriminating power.**  ``ALL_ABOVE_BASELINE`` and
``CONTINUOUS_ABOVE_BASELINE`` both fail against the pre-Stage-8 kernel (7/40
and 14/50 wrong selections plus a wrong final state).  ``MIXED_WITH_FAILURES``
does NOT — a failed mutation imputes a shifted reward of 0.0, which drove the
observation floor to 0.0 by accident and hid the defect.  It is kept because
it pins the failure-imputation and update semantics, but it is deliberately
recorded here as non-discriminating for the observation-range seed.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from noema.coordination.bandit.module import AsymmetricUCB

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bandit" / "donor_traces.json"
GOLDENS: Dict[str, Dict[str, Any]] = json.loads(_FIXTURE.read_text())

DEFAULT_CLONE = "/root/research_repos/ShinkaEvolve"
PINNED_COMMIT = "a81940026ef841113676b081090318b26a6a89b5"


# --------------------------------------------------------------------------
# tier 2 loader — the donor class, no donor package install
# --------------------------------------------------------------------------


def donor_class(clone_root: Optional[str] = None) -> Tuple[Optional[Any], Optional[str]]:
    """Return ``(AsymmetricUCB, None)`` or ``(None, reason)``. Never raises.

    ``shinka/llm/__init__.py`` imports the provider stack (anthropic, litellm),
    so the module is loaded BY PATH with the clone root on ``sys.path`` for its
    one intra-package import.  ``rich`` is stubbed ONLY when it is not installed:
    the donor imports it at module level but uses it only in ``print_summary``.
    Stubbing an installed ``rich`` would poison ``sys.modules`` for every later
    test in the session.  Both the stubs and the ``sys.path`` entry are scoped to
    the import attempt (Greptile finding on PR #108): they are removed in a
    ``finally`` once ``exec_module`` returns, whether it succeeds or raises, so
    the loader never leaves global import state behind.  This is safe because
    ``exec_module`` already binds the donor module's own references to those
    stub objects during execution; removing the ``sys.modules`` entries
    afterward does not affect the module the caller received.
    """
    root = Path(clone_root or os.environ.get("SHINKA_CLONE", DEFAULT_CLONE))
    source = root / "shinka" / "llm" / "prioritization.py"
    if not source.is_file():
        return None, f"ShinkaEvolve clone not found: {source} does not exist (pin {PINNED_COMMIT}; set SHINKA_CLONE to override)"
    try:
        import rich.box, rich.console, rich.table  # noqa: F401
        stubbed = []
    except ImportError:
        stubbed = [name for name in ("rich", "rich.table", "rich.console", "rich.box") if name not in sys.modules]
        for name in stubbed:
            sys.modules[name] = types.ModuleType(name)
        sys.modules["rich.table"].Table = object
        sys.modules["rich.console"].Console = object
    path_str = str(root)
    path_added = path_str not in sys.path
    if path_added:
        sys.path.insert(0, path_str)
    try:
        spec = importlib.util.spec_from_file_location("shinka_prioritization", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.AsymmetricUCB, None
    except Exception as exc:  # missing numpy/scipy, donor moved on, ...
        return None, f"ShinkaEvolve clone at {root} could not be loaded: {exc!r}"
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
        if path_added:
            try:
                sys.path.remove(path_str)
            except ValueError:
                pass


def require_donor(testcase: unittest.TestCase):
    cls, reason = donor_class()
    if cls is None:
        testcase.skipTest(f"TIER 2 NOT VERIFIED — {reason}")
    return cls


def make_donor(donor_cls, arms, c):
    """The neutralizing construction (see module docstring)."""
    return donor_cls(
        arm_names=list(arms), exploration_coef=c, epsilon=0.0,
        exponential_base=None, auto_decay=None, cost_aware_coef=0.0,
        shift_by_baseline=False, shift_by_parent=True,
        adaptive_scale=True, asymmetric_scaling=True,
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def replay(golden: Dict[str, Any], upto: Optional[int] = None):
    """Drive Noema down the golden's recorded arm sequence, collecting its picks."""
    ucb = AsymmetricUCB(golden["arms"], exploration_coef=golden["exploration_coef"])
    picks = []
    steps = zip(golden["picks"], golden["rewards"])
    for index, (pick, reward) in enumerate(steps):
        if upto is not None and index >= upto:
            break
        picks.append(ucb.select())
        ucb.update(pick, reward, baseline=golden["baseline"])
    return ucb, picks


def state_of(ucb: AsymmetricUCB) -> Dict[str, Any]:
    return {
        "sums": [round(x, 12) for x in ucb.sums],
        "counts": list(ucb.counts),
        "obs_min": ucb._obs_min,
        "obs_max": ucb._obs_max,
    }


def golden_state(golden: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sums": [round(x, 12) for x in golden["final_sums"]],
        "counts": list(golden["final_counts"]),
        "obs_min": golden["final_obs_min"],
        "obs_max": golden["final_obs_max"],
    }


# --------------------------------------------------------------------------
# tier 1 — donor-authored goldens, no clone needed
# --------------------------------------------------------------------------


class TestDonorGoldenTraces(unittest.TestCase):
    def test_selection_sequence_matches_the_donor(self):
        for name, golden in GOLDENS.items():
            with self.subTest(regime=name):
                _, picks = replay(golden)
                self.assertEqual(picks, golden["picks"])

    def test_final_state_matches_the_donor(self):
        for name, golden in GOLDENS.items():
            with self.subTest(regime=name):
                ucb, _ = replay(golden)
                self.assertEqual(state_of(ucb), golden_state(golden))

    def test_spread_steps_are_the_declared_deterministic_deviations(self):
        """Where the donor spreads probability, Noema takes the menu-first member.

        The donor returns a point mass except when some arm is unpulled
        ("unseen") or the top scores tie exactly ("tie"); in both cases it draws
        at random.  Noema resolves both deterministically — the ledgered
        epsilon-greedy/tie-break drops (module.py:49-51).  What is pinned here
        is that Noema's pick is always IN the donor's support and is its
        menu-first member, so the deviation is a choice within the donor's own
        admissible set, never outside it.
        """
        seen_kinds = set()
        for name, golden in GOLDENS.items():
            arms = golden["arms"]
            for index, spread in enumerate(golden["spread"]):
                if spread is None:
                    continue
                seen_kinds.add(spread["kind"])
                with self.subTest(regime=name, step=index, kind=spread["kind"]):
                    _, picks = replay(golden, upto=index + 1)
                    self.assertIn(picks[index], spread["support"])
                    menu_first = min(spread["support"], key=arms.index)
                    self.assertEqual(picks[index], menu_first)
        self.assertEqual(seen_kinds, {"unseen", "tie"}, "both spread kinds must be exercised")

    def test_checkpoint_midtrace_resumes_on_the_golden_path(self):
        """state_dict/load_state_dict must not perturb the donor-matched path."""
        for name, golden in GOLDENS.items():
            with self.subTest(regime=name):
                cut = len(golden["picks"]) // 2
                partial, _ = replay(golden, upto=cut)
                resumed = AsymmetricUCB(
                    golden["arms"], exploration_coef=golden["exploration_coef"]
                )
                resumed.load_state_dict(json.loads(json.dumps(partial.state_dict())))
                picks = []
                for pick, reward in list(zip(golden["picks"], golden["rewards"]))[cut:]:
                    picks.append(resumed.select())
                    resumed.update(pick, reward, baseline=golden["baseline"])
                self.assertEqual(picks, golden["picks"][cut:])
                self.assertEqual(state_of(resumed), golden_state(golden))


class TestObservationRangeSeed(unittest.TestCase):
    """Regression pins for the Stage 8 defect (undeclared, donor-divergent).

    The range used to seed at +/-inf regardless of `asymmetric`.  Under the
    asymmetric clip the shifted reward's support starts at 0, so the donor
    seeds at [0.0, 0.0] (prioritization.py:371-373).  Seeding at the sentinel
    made normalization depend on ARRIVAL ORDER and delayed adaptive scaling
    until two DISTINCT rewards had landed — changing which operator fires.
    """

    def test_asymmetric_seeds_the_range_at_zero(self):
        ucb = AsymmetricUCB(["a", "b"], exploration_coef=1.0)
        self.assertEqual((ucb._obs_min, ucb._obs_max), (0.0, 0.0))

    def test_non_asymmetric_keeps_the_no_observation_sentinel(self):
        ucb = AsymmetricUCB(["a", "b"], exploration_coef=1.0, asymmetric=False)
        self.assertEqual(ucb._obs_min, math.inf)
        self.assertEqual(ucb._obs_max, -math.inf)

    def test_first_reward_does_not_become_the_floor(self):
        """The discriminating assertion: pre-fix, the floor moved to 0.4."""
        ucb = AsymmetricUCB(["a", "b"], exploration_coef=1.0)
        ucb.update("a", reward=0.9, baseline=0.5)
        self.assertEqual(ucb._obs_min, 0.0)
        self.assertEqual(ucb._obs_max, 0.4)
        # One positive reward is enough to open the range (donor parity); the
        # pre-fix kernel needed a second, DISTINCT one.
        self.assertTrue(ucb._have_range())

    def test_range_update_is_gated_on_adaptive_scale(self):
        ucb = AsymmetricUCB(["a", "b"], exploration_coef=1.0, adaptive_scale=False)
        ucb.update("a", reward=0.9, baseline=0.5)
        self.assertEqual((ucb._obs_min, ucb._obs_max), (0.0, 0.0))

    def test_null_range_in_a_checkpoint_restores_to_the_seed(self):
        """A pre-fix checkpoint stored null; it must resume as a fresh bandit."""
        ucb = AsymmetricUCB(["a", "b"], exploration_coef=1.0)
        ucb.load_state_dict(
            {"arms": ["a", "b"], "sums": [0.0, 0.0], "counts": [0.0, 0.0],
             "obs_min": None, "obs_max": None}
        )
        self.assertEqual((ucb._obs_min, ucb._obs_max), (0.0, 0.0))


class TestTierTwoSkipDiscipline(unittest.TestCase):
    def test_tier_two_skip_is_loud(self):
        """A missing clone must yield a reason naming the path — never a silent pass."""
        cls, reason = donor_class("/nonexistent/shinka-clone")
        self.assertIsNone(cls)
        self.assertIn("/nonexistent/shinka-clone", reason)
        self.assertIn(PINNED_COMMIT, reason)

    def test_donor_loader_reports_success_or_reason_but_never_both(self):
        cls, reason = donor_class()
        self.assertNotEqual(cls is None, reason is None)


# --------------------------------------------------------------------------
# tier 2 — re-verify the goldens against the live donor
# --------------------------------------------------------------------------


class TestGoldensStillDescribeTheDonor(unittest.TestCase):
    def test_regenerating_from_the_clone_reproduces_every_golden(self):
        donor_cls = require_donor(self)
        import numpy as np

        for name, golden in GOLDENS.items():
            with self.subTest(regime=name):
                arms = golden["arms"]
                donor = make_donor(donor_cls, arms, golden["exploration_coef"])
                picks, spread = [], []
                for reward in golden["rewards"]:
                    probs = donor.posterior()
                    support = [arms[i] for i in range(len(arms)) if probs[i] > 0.0]
                    if len(support) > 1:
                        unseen = any(donor.n[i] <= 0.0 for i in range(len(arms)))
                        spread.append(
                            {"kind": "unseen" if unseen else "tie", "support": support}
                        )
                    else:
                        spread.append(None)
                    pick = arms[int(np.argmax(probs))]
                    picks.append(pick)
                    donor.update_submitted(pick)
                    donor.update(pick, reward, baseline=golden["baseline"])

                self.assertEqual(picks, golden["picks"])
                self.assertEqual(spread, golden["spread"])
                self.assertEqual(
                    [round(float(x), 12) for x in donor.s], golden["final_sums"]
                )
                self.assertEqual([float(x) for x in donor.divs], golden["final_counts"])
                self.assertEqual(float(donor._obs_min), golden["final_obs_min"])
                self.assertEqual(float(donor._obs_max), golden["final_obs_max"])


if __name__ == "__main__":
    unittest.main()
