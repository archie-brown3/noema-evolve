"""OpenEvolve islands + MAP-Elites population store."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from openevolve.config import DatabaseConfig
from openevolve.database import Program

from noema.substrate.base import PopulationSnapshot, Selection
from noema.substrate.database import SubstrateDatabase


class IslandsStore(SubstrateDatabase):
    capabilities = frozenset(
        {
            "population",
            "elites",
            "fitness",
            "code",
            "sampling_weights",
            "native_stock_selection",
        }
    )

    def __init__(self, config: DatabaseConfig):
        super().__init__(config)
        self.steps_per_generation = self.num_islands

    @property
    def scopes(self):
        return tuple(range(self.num_islands))

    def target_scope(self, iteration: int) -> int:
        return iteration % self.num_islands

    def population(self, scope=None) -> Sequence[Program]:
        if scope is None:
            return tuple(self._db.programs[pid] for pid in sorted(self._db.programs))
        index = int(scope) % self.num_islands
        return tuple(
            self._db.programs[pid]
            for pid in sorted(self._db.islands[index])
            if pid in self._db.programs
        )

    def elites(self, scope=None) -> Sequence[Program]:
        candidates = self.population(scope)
        return tuple(sorted(candidates, key=self.fitness, reverse=True)[:10])

    def top_programs(self, n: int, scope=None, island=None) -> Sequence[Program]:
        selected_scope = scope if scope is not None else island
        return super().top_programs(n, island=selected_scope)

    def per_scope_bests(self) -> Sequence[float]:
        return self.per_island_bests()

    def native_select(self, target_scope, num_inspirations: int) -> Selection:
        target = int(target_scope) % self.num_islands
        parent, inspirations = self._db.sample_from_island(
            target, num_inspirations=num_inspirations
        )
        source = parent.metadata.get("island", target)
        return Selection(parent, tuple(inspirations), source, target)

    def add(
        self,
        program: Program,
        iteration: Optional[int] = None,
        target_scope=None,
        target_island=None,
    ) -> str:
        target = target_scope if target_scope is not None else target_island
        return super().add(program, iteration=iteration, target_island=target)

    def snapshot(self, scope=None, limit: Optional[int] = None) -> PopulationSnapshot:
        programs = list(self.population(scope))
        programs.sort(key=self.fitness, reverse=True)
        if limit is not None:
            programs = programs[:limit]
        views = tuple(self.views(programs))
        return PopulationSnapshot(
            scope=scope,
            top_programs=views,
            fitnesses=tuple(self.fitness(program) for program in self.population(scope)),
            best_program=views[0] if views else None,
        )

    def state_dict(self) -> Dict[str, Any]:
        return {"steps_per_generation": self.steps_per_generation}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.steps_per_generation = int(state.get("steps_per_generation", self.num_islands))
