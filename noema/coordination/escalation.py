"""Escalation policy: WHEN a mutation generation escalates to the strong model.

The Advice.model plumbing (task 0107) lets any arm route one mutation call to a
different model. This module is the *policy* on top: a pure, deterministic unit
that, fed an EscalationContext each mutation, decides whether to escalate and
returns the escalation model name (during a burst) or None.

Design (approved 2026-07-27):
- Escalation lives on the MUTATION seat only. The coordination seat is
  untouched, so the single-model controlled-ablation contribution is preserved.
  (A future model-bandit over the coordination seat is deferred until after the
  headline experiments.)
- Five pluggable triggers decide when a burst starts. `random` reproduces
  OpenEvolve's weighted-model coin flip, giving the study a baseline to compare
  the adaptive triggers against.
- A trigger opens a fixed-length BURST (escalate for `burst_length` mutations),
  then reverts and enters a COOLDOWN (`cooldown_mutations` calls) before it can
  re-trigger — so escalation repeats when stagnation recurs without thrashing.

The policy owns only its burst/cooldown state (checkpointable); the histories it
thresholds are supplied by the host in the context, which already maintains them.
"""

import random
import statistics
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EscalationConfig:
    """Escalation policy settings. `trigger` picks the strategy; the rest are
    per-trigger knobs (ignored when not relevant to the chosen trigger)."""

    trigger: str = "plateau"  # plateau | invalidity | budget_fraction | diversity | random
    burst_length: int = 5     # mutations escalated per trigger
    cooldown_mutations: int = 20  # non-escalating calls before a re-trigger is allowed
    # trigger-specific:
    window: int = 10          # plateau / diversity lookback length
    min_delta: float = 0.001  # plateau: best-fitness change over the window counting as "flat"
    threshold: float = 0.5    # invalidity rate ceiling / diversity variance floor
    fraction: float = 0.7     # budget_fraction: escalate once this share of tokens is spent
    probability: float = 0.2  # random: per-call escalation probability (OpenEvolve baseline)
    # The strong model to route to. None disables escalation entirely (the
    # policy is inert). The host bootstraps this from the coordination seat.
    escalation_model: Optional[str] = None


@dataclass(frozen=True)
class EscalationContext:
    """Read-only per-mutation snapshot the host builds from its own histories +
    ledger. Fields default so a single-trigger caller only fills what it uses."""

    best_fitness_history: tuple = ()   # per-tick best fitness (plateau)
    recent_scores: tuple = ()          # recent child fitnesses (diversity)
    invalidity_rate: float = 0.0       # rolling fraction of invalid children (invalidity)
    tokens_spent: int = 0              # budget_fraction
    tokens_budget: int = 1
    iteration: int = 0


@dataclass
class EscalationState:
    """The policy's mutable, checkpointable state."""

    in_burst: bool = False
    burst_remaining: int = 0
    cooldown_remaining: int = 0
    trigger_count: int = 0


class EscalationPolicy:
    def __init__(self, config: EscalationConfig, rng: random.Random):
        if config.trigger not in self._TRIGGERS:
            raise ValueError(
                f"unknown escalation trigger {config.trigger!r}; "
                f"choose one of {sorted(self._TRIGGERS)}"
            )
        self.config = config
        self.rng = rng
        self.state = EscalationState()

    def step(self, ctx: EscalationContext) -> Optional[str]:
        """Return the escalation model for this mutation, or None to stay on the
        configured mutation model. Advances burst/cooldown state."""
        if self.config.escalation_model is None:
            return None
        if self.state.cooldown_remaining > 0:
            self.state.cooldown_remaining -= 1
            return None
        if self.state.in_burst:
            return self._emit_burst_call()
        if self._should_trigger(ctx):
            self.state.in_burst = True
            self.state.burst_remaining = self.config.burst_length
            self.state.trigger_count += 1
            return self._emit_burst_call()
        return None

    def _emit_burst_call(self) -> str:
        """Emit one escalated call; end the burst + start cooldown when spent."""
        self.state.burst_remaining -= 1
        if self.state.burst_remaining <= 0:
            self.state.in_burst = False
            self.state.cooldown_remaining = self.config.cooldown_mutations
        return self.config.escalation_model

    # --- triggers -----------------------------------------------------------

    def _should_trigger(self, ctx: EscalationContext) -> bool:
        return self._TRIGGERS[self.config.trigger](self, ctx)

    def _trigger_plateau(self, ctx: EscalationContext) -> bool:
        w = self.config.window
        hist = ctx.best_fitness_history
        if len(hist) < w:
            return False
        window = hist[-w:]
        return (window[-1] - window[0]) <= self.config.min_delta

    def _trigger_invalidity(self, ctx: EscalationContext) -> bool:
        return ctx.invalidity_rate >= self.config.threshold

    def _trigger_budget_fraction(self, ctx: EscalationContext) -> bool:
        if ctx.tokens_budget <= 0:
            return False
        return ctx.tokens_spent / ctx.tokens_budget >= self.config.fraction

    def _trigger_diversity(self, ctx: EscalationContext) -> bool:
        recent = ctx.recent_scores[-self.config.window:]
        if len(recent) < 2:
            return False
        return statistics.pvariance(recent) < self.config.threshold

    def _trigger_random(self, ctx: EscalationContext) -> bool:
        return self.rng.random() < self.config.probability

    _TRIGGERS = {
        "plateau": _trigger_plateau,
        "invalidity": _trigger_invalidity,
        "budget_fraction": _trigger_budget_fraction,
        "diversity": _trigger_diversity,
        "random": _trigger_random,
    }

    # --- checkpointing ------------------------------------------------------

    def state_dict(self) -> dict:
        return {
            "in_burst": self.state.in_burst,
            "burst_remaining": self.state.burst_remaining,
            "cooldown_remaining": self.state.cooldown_remaining,
            "trigger_count": self.state.trigger_count,
        }

    def load_state_dict(self, state: dict) -> None:
        self.state = EscalationState(
            in_burst=state["in_burst"],
            burst_remaining=state["burst_remaining"],
            cooldown_remaining=state["cooldown_remaining"],
            trigger_count=state["trigger_count"],
        )
