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
- Selection influence is requested synchronously before sampling; post-selection
  Advice contains prompt guidance only.
- Modules receive their LLM handle (a BudgetedLLM bound to the "coordination"
  ledger account) and their RNG at construction; they must not create either.
"""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional

from noema.base import PopulationSnapshot
from noema.views import ProgramView


class Outcome(str, Enum):
    """Why report_result was called — the credit-assignment category (task 0090).

    A `str` enum so it JSON-serializes verbatim and compares equal to its value.

    `child is None` alone conflated two outcomes that are opposite evidence about
    a mutation operator: the model produced no applyable program at all, versus it
    produced runnable code that then failed at evaluation. An outcome-driven
    mechanism (the AsymmetricUCB bandit, task 0073) must tell them apart. This is
    additive and keyword-only: an arm that ignores it is byte-for-byte unchanged,
    which is why null/hifo/pes are unaffected.
    """

    ACCEPTED = "accepted"        # a real evaluated child (may still have scored worse)
    NO_PROGRAM = "no_program"    # unparseable response or over-length code — never evaluated
    EVAL_ERROR = "eval_error"    # applyable code that errored/produced no metrics at evaluation


@dataclass(frozen=True, init=False)
class GenerationContext:
    """Read-only evolution state handed to the module by the host loop"""

    iteration: int  # global mutation counter
    generation: int
    scope_id: Any
    parent: Optional[ProgramView]
    inspirations: tuple[ProgramView, ...]
    local_population: PopulationSnapshot
    global_population: PopulationSnapshot
    # Host-maintained histories, one entry per generation tick; definitions are
    # fixed per experiment (see controller) and identical across arms
    best_fitness_history: tuple[float, ...]
    avg_fitness_history: tuple[float, ...]
    diversity_history: tuple[float, ...]
    # The mutation operator the host drew for this offspring (a menu name like
    # "e1"/"m2"), or None on the legacy no-menu path and at generation ticks.
    # Sanctioned addition #2 (Decision #51): read-only metadata flowing
    # selection -> prompt so a module can apply operator-specific prompt
    # wording (HiFo's per-operator suffix variants). The REVERSE direction —
    # a module steering operator selection — remains banned (Decision #49).
    operator: Optional[str]

    def __init__(
        self,
        iteration: int,
        generation: int,
        scope_id: Any = None,
        parent: Optional[ProgramView] = None,
        inspirations: Iterable[ProgramView] = (),
        local_population: Optional[PopulationSnapshot] = None,
        global_population: Optional[PopulationSnapshot] = None,
        best_fitness_history: Iterable[float] = (),
        avg_fitness_history: Iterable[float] = (),
        diversity_history: Iterable[float] = (),
        operator: Optional[str] = None,
        # Compatibility inputs for pre-task-0074 fixtures. They are translated
        # immediately and are intentionally absent from dataclass fields.
        island: Any = None,
        top_programs: Iterable[ProgramView] = (),
        island_fitnesses: Iterable[float] = (),
    ):
        if scope_id is None:
            scope_id = island
        if local_population is None:
            top = tuple(top_programs)
            local_population = PopulationSnapshot(
                scope=scope_id,
                top_programs=top,
                fitnesses=tuple(island_fitnesses),
                best_program=top[0] if top else None,
            )
        if global_population is None:
            global_population = PopulationSnapshot(scope=None)
        object.__setattr__(self, "iteration", iteration)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "scope_id", scope_id)
        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "inspirations", tuple(inspirations))
        object.__setattr__(self, "local_population", local_population)
        object.__setattr__(self, "global_population", global_population)
        object.__setattr__(self, "best_fitness_history", tuple(best_fitness_history))
        object.__setattr__(self, "avg_fitness_history", tuple(avg_fitness_history))
        object.__setattr__(self, "diversity_history", tuple(diversity_history))
        object.__setattr__(self, "operator", operator)

@dataclass(frozen=True)
class SelectionContext:
    iteration: int
    generation: int
    global_population: Optional[PopulationSnapshot]
    scope_id: Any = None
    local_population: Optional[PopulationSnapshot] = None


@dataclass(frozen=True)
class SamplingRequest:
    hints: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Advice:
    """Module output for one mutation. Default Advice() is a no-op (the OFF arm)."""

    prompt_block: str = ""  # appended to the mutation user prompt ("" = nothing appended)
    system_block: str = ""  # optional system-message suffix
    # Opaque payload the host stores on the child program's metadata and hands
    # back in report_result (e.g. which insights were injected)
    attribution: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProposedProgram:
    """A whole program a module authored at a generation tick, for the HOST to
    evaluate and insert (Punctuated Equilibrium, task 0109).

    Sanctioned addition #3: a coordination module may *propose* programs, but it
    never evaluates or inserts them itself — the host does, through the same
    metered, deterministic loop it uses for ordinary mutations. This preserves
    the metering guarantee (the module's generation calls are already billed to
    `self.llm`'s coordination account) and determinism (the host owns eval order
    and RNG). `origin` is stamped on the child's metadata; `parent_id` records
    lineage. Additive: a module that returns no Intervention is unaffected.
    """

    code: str
    origin: str = "coordination"     # provenance tag, e.g. "paradigm_shift" / "variant"
    parent_id: Optional[str] = None


@dataclass(frozen=True)
class Intervention:
    """A module's proposed programs at a generation tick (see ProposedProgram)."""

    proposals: tuple["ProposedProgram", ...] = ()


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

    def sampling_request(self, ctx: SelectionContext) -> SamplingRequest:
        """Declarative selection hints, synchronously requested before sampling."""
        return SamplingRequest()

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
        *,
        outcome: Outcome = Outcome.ACCEPTED,
    ) -> None:
        """
        Called once per iteration, after the retry loop (credit assignment).

        child is the evaluated ProgramView on success, or None when no evaluable
        program survived the iteration. `eval_failed` is True whenever child is
        None. `outcome` (task 0090) refines that None:
        - ACCEPTED   — a real evaluated child (child is not None; it may still
                       have scored worse than the parent — that is a normal
                       accepted result, visible via its metrics)
        - NO_PROGRAM — the model produced nothing applyable (unparseable, over-length)
        - EVAL_ERROR — applyable code that errored or yielded no metrics at evaluation

        `outcome` is keyword-only with a default: a module that ignores it behaves
        exactly as before its introduction. Only outcome-driven mechanisms (the
        bandit) need it; null/hifo/pes do not read it.
        """

    @abstractmethod
    async def on_generation_end(self, ctx: GenerationContext) -> Optional["Intervention"]:
        """
        Generation tick. May make coordination LLM calls (HiFo: insight
        extraction) — hence async.

        Returns None (the default for every arm to date) or an `Intervention`
        carrying whole programs the module authored for the HOST to evaluate and
        insert (Punctuated Equilibrium, task 0109). Returning None is unchanged
        behaviour, so null/hifo/pes/bandit are byte-for-byte unaffected.
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

    def report_result(
        self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED
    ) -> None:
        return None

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        return None

    def state_dict(self) -> Dict[str, Any]:
        return {}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        return None
