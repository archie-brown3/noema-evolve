"""A non-island population store, for cross-substrate contract tests.

Task 0080. The neutral seam was designed against exactly one concrete store, so
nothing falsified it. This is the falsifier: an in-memory store with a genuinely
different topology (behavioural regions over CVT-style cells), implementing the
same `PopulationStore` protocol. It is a test fixture, not a production
substrate — task 0037 (TreeStore) and any real CVT store are separate work, and
this file must never be imported by `noema/`.

It exists to answer one question mechanically: *can a coordination module that
was written against islands run, unmodified, on a store that is not islands?*
If a module reaches for anything island-shaped, it fails here rather than in the
first tree run.

The granularity decision the design note argues for is baked in deliberately:
a CVT **cell** holds one elite, so a cell-shaped local cohort would hand HiFo a
length-1 fitness "distribution" and PES a single-program population. A
**region** — a group of cells — is therefore the neutral unit, and it is what
this store scopes by.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from openevolve.database import Program

from noema.base import PopulationSnapshot, RegionSummary, Selection
from noema.views import ProgramView


class FixtureCVTStore:
    """Behavioural regions over cells. Deliberately not islands."""

    topology = "cvt_regions"
    capabilities = frozenset({"population", "elites", "fitness", "code", "regions"})
    feature_dimensions: Sequence[str] = ()

    def __init__(self, num_regions: int = 3, cells_per_region: int = 4):
        self.num_regions = num_regions
        self.cells_per_region = cells_per_region
        self.steps_per_generation = num_regions * cells_per_region
        # region index -> list of programs (each region groups several cells, so
        # a region holds a population, not a single elite)
        self._regions: Dict[int, List[Program]] = {i: [] for i in range(num_regions)}
        self._programs: Dict[str, Program] = {}
        self._artifacts: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------- topology

    def target_scope(self, iteration: int) -> int:
        # Not `iteration % num_islands`: a different cadence on purpose, so any
        # coordinator that assumed the island scheduling rule shows up as a bug.
        return (iteration // self.cells_per_region) % self.num_regions

    @property
    def num_programs(self) -> int:
        return len(self._programs)

    def regions(self) -> Sequence[RegionSummary]:
        return tuple(
            RegionSummary(
                scope=index,
                label=f"region_{index}",
                best_fitness=max(
                    (self.fitness(p) for p in self._regions[index]), default=0.0
                ),
                size=len(self._regions[index]),
            )
            for index in range(self.num_regions)
        )

    def per_scope_bests(self) -> Sequence[float]:
        return tuple(r.best_fitness for r in self.regions())

    # ---------------------------------------------------------- population

    def population(self, scope: Any = None) -> Sequence[Program]:
        if scope is None:
            return tuple(self._programs[pid] for pid in sorted(self._programs))
        return tuple(self._regions[int(scope) % self.num_regions])

    def elites(self, scope: Any = None) -> Sequence[Program]:
        return tuple(sorted(self.population(scope), key=self.fitness, reverse=True)[:10])

    def top_programs(self, n: int, scope: Any = None) -> Sequence[Program]:
        return tuple(sorted(self.population(scope), key=self.fitness, reverse=True)[:n])

    def best_program(self) -> Optional[Program]:
        programs = self.population(None)
        return max(programs, key=self.fitness) if programs else None

    def fitness(self, program: Program) -> float:
        return float(program.metrics.get("combined_score", 0.0))

    def all_fitnesses(self) -> Sequence[float]:
        return tuple(self.fitness(p) for p in self.population(None))

    def add(
        self,
        program: Program,
        iteration: Optional[int] = None,
        target_scope: Any = None,
    ) -> str:
        scope = 0 if target_scope is None else int(target_scope) % self.num_regions
        program.metadata = dict(program.metadata or {})
        program.metadata["region"] = scope
        self._regions[scope].append(program)
        self._programs[program.id] = program
        return program.id

    def store_artifacts(self, program_id: str, artifacts: Mapping[str, Any]) -> None:
        self._artifacts[program_id] = dict(artifacts)

    # ------------------------------------------------------------- views

    def view(self, program: Program) -> ProgramView:
        return ProgramView.from_program(program, list(self.feature_dimensions))

    def views(self, programs: Sequence[Program]) -> Sequence[ProgramView]:
        return tuple(self.view(p) for p in programs)

    def snapshot(self, scope: Any = None, limit: Optional[int] = None) -> PopulationSnapshot:
        programs = sorted(self.population(scope), key=self.fitness, reverse=True)
        if limit is not None:
            programs = programs[:limit]
        views = self.views(programs)
        return PopulationSnapshot(
            scope=scope,
            top_programs=tuple(views),
            fitnesses=tuple(self.fitness(p) for p in self.population(scope)),
            best_program=views[0] if views else None,
            topology=self.topology,
            regions=tuple(self.regions()) if scope is None else (),
        )

    # ------------------------------------------------------------ selection

    def native_select(self, target_scope: Any, num_inspirations: int) -> Selection:
        candidates = list(self.population(target_scope)) or list(self.population(None))
        if not candidates:
            raise ValueError("empty store")
        parent = max(candidates, key=self.fitness)
        inspirations = tuple(
            p for p in self.top_programs(num_inspirations + 1) if p.id != parent.id
        )[:num_inspirations]
        source = parent.metadata.get("region", target_scope)
        return Selection(parent, inspirations, source, target_scope)

    # ----------------------------------------------------------- lifecycle

    def end_generation(self) -> bool:
        return False

    def save(self, path: str, iteration: int = 0) -> None:
        return None

    def load(self, path: str) -> None:
        return None

    def state_dict(self) -> Dict[str, Any]:
        return {"steps_per_generation": self.steps_per_generation}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.steps_per_generation = int(
            state.get("steps_per_generation", self.steps_per_generation)
        )


def seed_store(store, scores_by_scope: Mapping[int, Sequence[float]]) -> None:
    """Populate any PopulationStore with fitnesses, per scope."""
    for scope, scores in scores_by_scope.items():
        for score in scores:
            store.add(
                Program(
                    id=str(uuid.uuid4()),
                    code=f"def f():\n    return {score}\n",
                    language="python",
                    metrics={"combined_score": score},
                ),
                target_scope=scope,
            )
