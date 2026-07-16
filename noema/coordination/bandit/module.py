"""
The `bandit` mechanism arm: AsymmetricUCB over the mutation-operator menu.

The only ZERO-TOKEN mechanism on the axis — it makes no coordination LLM call
and injects no prompt text. It steers evolution solely by choosing WHICH operator
fires each iteration, requested through the pre-selection hook `sampling_request`.
Alongside Null it anchors the zero-coordination-token end of the mechanism axis
(task 0073, spec/DELIVERABLES.md §3.2).

Reward (fixed for the study): the parent-shifted, asymmetrically-clipped fitness
delta `max(child_fitness - parent_fitness, 0)` — ShinkaEvolve's own scheme. A
worse-but-valid child and a failed mutation both yield 0; the operator is credited
only for realized improvement. The task-0090 `outcome` is recorded per pull for the
run log (an operator's failure composition — e.g. "e2 fails to parse 30% of the
time" — is a genuine finding) but does not shape the reward, so the reward stays
the single fitness-based signal the ticket fixes.
"""

import logging
import math
import random
from typing import Any, Dict, List, Optional

from noema.coordination.base import (
    Advice,
    CoordinationModule,
    GenerationContext,
    Outcome,
    SamplingRequest,
    SelectionContext,
)
from noema.views import ProgramView

logger = logging.getLogger(__name__)

DEFAULT_OPERATORS = ["e1", "e2", "m1", "m2", "m3"]


# =============================================================================
# BORROWED CODE — AsymmetricUCB selection score, ported from ShinkaEvolve
# (Apache-2.0). Source: https://github.com/SakanaAI/ShinkaEvolve
#   shinka/llm/prioritization.py  (class AsymmetricUCB, _normalized_means,
#   update; pinned at commit a81940026ef841113676b081090318b26a6a89b5)
# Change ledger — every deviation marked # NOEMA:
#   - cost-blind only (spec §3.2 "cost-blind first"): the cost_aware / cost_ratio
#     machinery is dropped, not ported.
#   - exponential + posterior-sampling scaling dropped; the additive
#     `normalized_mean + c*sqrt(2 ln t / n)` score is the retained core.
#   - epsilon-greedy exploration (ShinkaEvolve default 0.2) OMITTED for study
#     determinism: pure UCB1 argmax with deterministic (menu-order) tie-break, so
#     the arm is reproducible from the reward stream alone with no RNG draw.
#   - numpy replaced with the stdlib so the arm has no new dependency.
# =============================================================================


class AsymmetricUCB:
    """Cost-blind AsymmetricUCB over a fixed set of named arms.

    `asymmetric` clips each parent-shifted reward at 0 (you are not punished for
    a worse child, only rewarded for a better one); `adaptive_scale` min-max
    normalizes the per-arm means into the exploitation term, so the exploration
    bonus and exploitation term stay on a comparable scale.
    """

    def __init__(
        self,
        arm_names: List[str],
        exploration_coef: float = 1.0,
        asymmetric: bool = True,
        adaptive_scale: bool = True,
    ):
        if not arm_names:
            raise ValueError("AsymmetricUCB needs at least one arm")
        self.arms: List[str] = list(arm_names)
        self._index: Dict[str, int] = {name: i for i, name in enumerate(self.arms)}
        n = len(self.arms)
        self.sums: List[float] = [0.0] * n     # sum of shifted rewards per arm
        self.counts: List[float] = [0.0] * n   # completed pulls per arm
        self.c = float(exploration_coef)
        self.asymmetric = bool(asymmetric)
        self.adaptive_scale = bool(adaptive_scale)
        self._obs_min = math.inf
        self._obs_max = -math.inf

    # -- scoring ---------------------------------------------------------------

    def _mean(self, i: int) -> float:
        return self.sums[i] / self.counts[i] if self.counts[i] > 0 else 0.0

    def _have_range(self) -> bool:
        return (
            math.isfinite(self._obs_min)
            and math.isfinite(self._obs_max)
            and self._obs_max > self._obs_min
        )

    def _normalized_mean(self, i: int) -> float:
        m = self._mean(i)
        if not self.adaptive_scale or not self._have_range():
            return m
        return (m - self._obs_min) / max(self._obs_max - self._obs_min, 1e-9)

    def select(self) -> str:
        """Deterministic UCB1: every arm once, then argmax of
        normalized_mean + c*sqrt(2 ln t / n). Menu-order tie-break."""
        # NOEMA: unseen arms first, in fixed menu order (ShinkaEvolve draws them
        # uniformly at random; deterministic order removes the only RNG use).
        for i, count in enumerate(self.counts):
            if count <= 0:
                return self.arms[i]
        t = float(sum(self.counts))
        num = 2.0 * math.log(max(t, 2.0))
        best_i, best_score = 0, -math.inf
        for i in range(len(self.arms)):
            score = self._normalized_mean(i) + self.c * math.sqrt(num / self.counts[i])
            if score > best_score:  # strict > keeps the first (menu-order) winner
                best_i, best_score = i, score
        return self.arms[best_i]

    # -- update ----------------------------------------------------------------

    def update(self, arm: str, reward: Optional[float], baseline: float = 0.0) -> float:
        """Record a pull. reward=None imputes the worst (a failed mutation): the
        shifted reward is 0 after clipping, which still counts as a pull and so
        lowers the arm's mean — penalizing failure-prone operators."""
        i = self._index[arm]
        is_real = reward is not None
        r = (float(reward) - baseline) if is_real else 0.0
        if self.asymmetric:
            r = max(r, 0.0)
        self.sums[i] += r
        self.counts[i] += 1.0
        if is_real:
            self._obs_min = min(self._obs_min, r)
            self._obs_max = max(self._obs_max, r)
        return r

    # -- checkpoint ------------------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {
            "arms": list(self.arms),
            "sums": list(self.sums),
            "counts": list(self.counts),
            "obs_min": self._obs_min if math.isfinite(self._obs_min) else None,
            "obs_max": self._obs_max if math.isfinite(self._obs_max) else None,
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        arms = list(state.get("arms", self.arms))
        if arms != self.arms:
            raise ValueError(
                f"bandit arm layout changed on resume: {arms} != {self.arms}"
            )
        self.sums = [float(x) for x in state["sums"]]
        self.counts = [float(x) for x in state["counts"]]
        self._obs_min = math.inf if state.get("obs_min") is None else float(state["obs_min"])
        self._obs_max = -math.inf if state.get("obs_max") is None else float(state["obs_max"])


# ============================== END BORROWED =================================


class BanditModule(CoordinationModule):
    """Zero-token operator-routing arm. See module docstring for the reward."""

    def __init__(self, config=None, llm=None, rng=None):
        super().__init__(config=config, llm=llm, rng=rng)
        operators = self.config.get("operators", DEFAULT_OPERATORS)
        self.ucb = AsymmetricUCB(
            arm_names=list(operators),
            exploration_coef=float(self.config.get("exploration_coef", 1.0)),
            asymmetric=bool(self.config.get("asymmetric", True)),
            adaptive_scale=bool(self.config.get("adaptive_scale", True)),
        )
        # The operator requested this iteration, credited when its result reports.
        # Safe as transient state: the loop is strictly sequential (one mutation
        # in flight) and checkpoints only at generation ticks, between iterations.
        self._pending: Optional[str] = None
        # Per-arm outcome tallies for the run log (analysis only, not reward).
        self._outcomes: Dict[str, Dict[str, int]] = {
            name: {o.value: 0 for o in Outcome} for name in self.ucb.arms
        }

    # -- pre-selection: steer the operator, nothing else -----------------------

    def sampling_request(self, ctx: SelectionContext) -> SamplingRequest:
        arm = self.ucb.select()
        self._pending = arm
        return SamplingRequest(hints={"operator": arm})

    async def advise(self, ctx: GenerationContext) -> Advice:
        return Advice()  # zero-token: no prompt guidance, ever

    # -- credit assignment -----------------------------------------------------

    def report_result(
        self,
        ctx: GenerationContext,
        child: Optional[ProgramView],
        attribution: Dict[str, Any],
        eval_failed: bool,
        *,
        outcome: Outcome = Outcome.ACCEPTED,
    ) -> None:
        arm = self._pending
        self._pending = None
        if arm is None:
            return  # no request was made this iteration (defensive)

        parent_fitness = ctx.parent.fitness if ctx.parent is not None else 0.0
        # ACCEPTED -> real fitness; any failure -> None (imputed worst). "Scored
        # worse" is an ACCEPTED child whose delta clips to 0 (module docstring).
        reward = child.fitness if (child is not None and not eval_failed) else None
        self.ucb.update(arm, reward=reward, baseline=parent_fitness)

        tally = self._outcomes.get(arm)
        if tally is not None:
            key = outcome.value if isinstance(outcome, Outcome) else str(outcome)
            tally[key] = tally.get(key, 0) + 1

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        return None

    # -- checkpoint / observability --------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {"ucb": self.ucb.state_dict(), "outcomes": self._outcomes}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.ucb.load_state_dict(state["ucb"])
        if "outcomes" in state:
            self._outcomes = {
                arm: dict(counts) for arm, counts in state["outcomes"].items()
            }

    def log_snapshot(self) -> Dict[str, Any]:
        return {
            "arms": list(self.ucb.arms),
            "counts": list(self.ucb.counts),
            "means": [self.ucb._mean(i) for i in range(len(self.ucb.arms))],
            "outcomes": self._outcomes,
        }
