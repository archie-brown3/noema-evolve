"""Neutral population-store and parent-selection contracts.

Population topology and selection policy are peer components.  Concrete stores
provide read-only candidate views and persistence; policies choose parents;
``SubstrateRuntime`` is the only compositor used by the controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

from openevolve.database import Program

from noema.views import ProgramView


ScopeId = Any


@dataclass(frozen=True)
class Selection:
    parent: Any
    inspirations: Tuple[Any, ...] = ()
    source_scope: ScopeId = None
    target_scope: ScopeId = None


@dataclass(frozen=True)
class RegionSummary:
    """One meaningful sub-population, named by the substrate that owns it.

    A region is the neutral unit of "somewhere else in the population that a
    coordinator may reason about".  Islands map one region per island; a CVT
    store maps a *group* of cells (a single cell holds one elite, so cell-per-
    region would make ``fitnesses`` degenerate); a tree maps a branch.  The
    substrate supplies ``label`` — coordination renders it, never invents it.
    """

    scope: ScopeId
    label: str
    best_fitness: float
    size: int = 0


@dataclass(frozen=True)
class PopulationSnapshot:
    scope: ScopeId
    top_programs: Tuple[ProgramView, ...] = ()
    fitnesses: Tuple[float, ...] = ()
    best_program: Optional[ProgramView] = None
    # Substrate-declared topology name ("islands", "cvt_regions", "tree_branches").
    # A coordinator may branch on it, but that adaptation is then part of the arm
    # definition and must be declared (see the decoupling design note).
    topology: str = "unstructured"
    # Populated on the global snapshot only; empty on a local cohort snapshot.
    regions: Tuple[RegionSummary, ...] = ()


@runtime_checkable
class PopulationStore(Protocol):
    steps_per_generation: int
    capabilities: frozenset[str]
    feature_dimensions: Sequence[str]
    num_programs: int
    topology: str

    def target_scope(self, iteration: int) -> ScopeId: ...
    def population(self, scope: ScopeId = None) -> Sequence[Program]: ...
    def elites(self, scope: ScopeId = None) -> Sequence[Program]: ...
    def native_select(self, target_scope: ScopeId, num_inspirations: int) -> Selection: ...
    def add(
        self,
        program: Program,
        iteration: Optional[int] = None,
        target_scope: ScopeId = None,
    ) -> str: ...
    def snapshot(
        self, scope: ScopeId = None, limit: Optional[int] = None
    ) -> PopulationSnapshot: ...
    def top_programs(self, n: int, scope: ScopeId = None) -> Sequence[Program]: ...
    def best_program(self) -> Optional[Program]: ...
    def fitness(self, program: Program) -> float: ...
    def all_fitnesses(self) -> Sequence[float]: ...
    def per_scope_bests(self) -> Sequence[float]: ...
    def regions(self) -> Sequence[RegionSummary]: ...
    def view(self, program: Program) -> ProgramView: ...
    def views(self, programs: Sequence[Program]) -> Sequence[ProgramView]: ...
    def store_artifacts(self, program_id: str, artifacts: Mapping[str, Any]) -> None: ...
    def end_generation(self) -> bool: ...
    def save(self, path: str, iteration: int = 0) -> None: ...
    def load(self, path: str) -> None: ...
    def state_dict(self) -> Dict[str, Any]: ...
    def load_state_dict(self, state: Mapping[str, Any]) -> None: ...


@runtime_checkable
class TreeTopology(Protocol):
    """Small read-only topology capability consumed by tree policies."""

    def tree_root_id(self) -> Optional[str]: ...
    def tree_children(self, program_id: str) -> Sequence[str]: ...


@runtime_checkable
class TokenClockObserver(Protocol):
    """Optional policy capability for the host's cumulative token clock."""

    def set_tokens_spent(self, tokens_spent: int) -> None: ...


@runtime_checkable
class SelectionPolicy(Protocol):
    required_capabilities: frozenset[str]
    supported_hints: frozenset[str]

    def select(
        self,
        store: PopulationStore,
        *,
        target_scope: ScopeId = None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection: ...

    def on_child_accepted(self, *, parent: Any, child: Any, step_size: float) -> None: ...
    def on_child_rejected(
        self, *, parent: Any, child: Any = None, eval_failed: bool
    ) -> None: ...
    def state_dict(self) -> Dict[str, Any]: ...
    def load_state_dict(self, state: Mapping[str, Any]) -> None: ...


class SubstrateRuntime:
    """Compose a store and policy without either concrete type owning the other."""

    store: PopulationStore
    policy: SelectionPolicy

    def __init__(self, store: PopulationStore, policy: SelectionPolicy):
        missing = policy.required_capabilities - store.capabilities
        if missing:
            raise ValueError(
                f"selection policy requires unsupported store capabilities: {sorted(missing)}"
            )
        self.store = store
        self.policy = policy
        self.last_selection_trace: Dict[str, Any] = {
            "requested": {},
            "honored": {},
            "ignored": {},
        }

    @property
    def steps_per_generation(self) -> int:
        return self.store.steps_per_generation

    def target_scope(self, iteration: int) -> ScopeId:
        return self.store.target_scope(iteration)

    def select(
        self,
        *,
        target_scope: ScopeId = None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection:
        requested = dict(hints or {})
        supported = self.policy.supported_hints
        self.last_selection_trace = {
            "requested": requested,
            "honored": {
                key: value for key, value in requested.items() if key in supported
            },
            "ignored": {
                key: value for key, value in requested.items() if key not in supported
            },
        }
        return self.policy.select(
            self.store,
            target_scope=target_scope,
            num_inspirations=num_inspirations,
            hints=hints,
        )

    def on_child_accepted(self, *, parent: Any, child: Any, step_size: float) -> None:
        self.policy.on_child_accepted(parent=parent, child=child, step_size=step_size)

    def on_child_rejected(
        self, *, parent: Any, child: Any = None, eval_failed: bool
    ) -> None:
        self.policy.on_child_rejected(parent=parent, child=child, eval_failed=eval_failed)

    def set_tokens_spent(self, tokens_spent: int) -> None:
        if isinstance(self.policy, TokenClockObserver):
            self.policy.set_tokens_spent(tokens_spent)

    def state_dict(self) -> Dict[str, Any]:
        return {
            "policy": self.policy.state_dict(),
            "last_selection_trace": self.last_selection_trace,
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.policy.load_state_dict(state.get("policy", {}))
        self.last_selection_trace = dict(
            state.get("last_selection_trace", self.last_selection_trace)
        )

    def log_snapshot(self) -> Dict[str, Any]:
        return dict(self.last_selection_trace)
