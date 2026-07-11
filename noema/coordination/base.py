"""
The CoordinationModule interface (PLAN.md section 3.1).

A coordination mechanism observes evolution state, injects text into mutation
prompts, and receives credit-assignment feedback. Coordination-present vs
coordination-absent is a single controlled variable: the host loop is identical
across arms, only the module differs (NullCoordination = the OFF arm).

Design constraints:
- Mechanism-specific semantics (HiFo's insights/regime/directive, a bandit's
  arm indices, ...) live in Advice.prompt_block and the opaque `attribution`
  payload — the host stores and returns attribution verbatim, never interprets it.
- `sampling_hint` is a hint, not a command: the host honors keys it understands
  and logs what it honored, so the loop stays identical across arms.
- Modules receive their LLM handle (a BudgetedLLM bound to the "coordination"
  ledger account) and their RNG at construction; they must not create either.
"""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from noema.substrate.views import ProgramView


@dataclass(frozen=True)
class GenerationContext:
    """Read-only evolution state handed to the module by the host loop"""

    iteration: int  # global mutation counter
    generation: int  # completed generation ticks (one tick per num_islands iterations)
    island: int
    parent: Optional[ProgramView]
    inspirations: List[ProgramView] = field(default_factory=list)
    top_programs: List[ProgramView] = field(default_factory=list)  # island-local, best first
    island_fitnesses: List[float] = field(default_factory=list)  # all fitnesses on the island
    # Host-maintained histories, one entry per generation tick; definitions are
    # fixed per experiment (see controller) and identical across arms
    best_fitness_history: List[float] = field(default_factory=list)
    avg_fitness_history: List[float] = field(default_factory=list)
    diversity_history: List[float] = field(default_factory=list)


@dataclass
class Advice:
    """Module output for one mutation. Default Advice() is a no-op (the OFF arm)."""

    prompt_block: str = ""  # appended to the mutation user prompt ("" = nothing appended)
    system_block: str = ""  # optional system-message suffix
    # Opaque payload the host stores on the child program's metadata and hands
    # back in report_result (e.g. which insights were injected)
    attribution: Dict[str, Any] = field(default_factory=dict)
    # OPTIONAL selection influence for future mechanisms (e.g. bandit samplers);
    # HiFo never sets it and the host may ignore unknown keys
    sampling_hint: Optional[Dict[str, Any]] = None


class CoordinationModule(ABC):
    """Base class for coordination mechanisms"""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        llm=None,  # BudgetedLLM bound to the "coordination" account; None if unused
        rng: Optional[random.Random] = None,
    ):
        self.config = config or {}
        self.llm = llm
        self.rng = rng or random.Random()

    @abstractmethod
    async def advise(self, ctx: GenerationContext) -> Advice:
        """Called once per mutation, before the mutation LLM call"""

    @abstractmethod
    def report_result(
        self,
        ctx: GenerationContext,
        child: Optional[ProgramView],
        attribution: Dict[str, Any],
        eval_failed: bool,
    ) -> None:
        """
        Called once per mutation attempt, after evaluation (credit assignment).

        child is None when no evaluable program was produced (unparseable LLM
        response, over-length code); eval_failed also covers evaluation errors.
        """

    @abstractmethod
    async def on_generation_end(self, ctx: GenerationContext) -> None:
        """
        Generation tick. May make coordination LLM calls (HiFo: insight
        extraction) — hence async.
        """

    @abstractmethod
    def state_dict(self) -> Dict[str, Any]:
        """JSON-serializable state for checkpointing"""

    @abstractmethod
    def load_state_dict(self, state: Dict[str, Any]) -> None:
        """Restore from a state_dict() (checkpoint resume)"""

    def log_snapshot(self) -> Dict[str, Any]:
        """Per-generation JSON for the run log; override for mechanism-specific fields"""
        return {}

    async def retry_advice(
        self, ctx: GenerationContext, error_text: str, attempt: int
    ) -> str:
        """Text to append to a retry's mutation prompt (default: none).

        Called by the controller's retry loop after a failed attempt, before
        re-issuing the mutation call. "" means the retry uses raw error only.
        Non-abstract on purpose: NullCoordination, HiFo, and s1 inherit this
        no-op; only PES overrides it (the sanctioned second-consumer case).
        """
        return ""


class NullCoordination(CoordinationModule):
    """The coordination-OFF arm: injects nothing, learns nothing"""

    async def advise(self, ctx: GenerationContext) -> Advice:
        return Advice()

    def report_result(self, ctx, child, attribution, eval_failed) -> None:
        return None

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        return None

    def state_dict(self) -> Dict[str, Any]:
        return {}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        return None
