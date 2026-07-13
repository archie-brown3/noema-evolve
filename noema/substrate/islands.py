"""OpenEvolve islands + MAP-Elites population store."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from openevolve.config import DatabaseConfig
from openevolve.database import Program

from noema.substrate.base import PopulationSnapshot, RegionSummary, Selection
from noema.substrate.database import SubstrateDatabase


class IslandsStore(SubstrateDatabase):
    topology = "islands"
    capabilities = frozenset(
        {
            "population",
            "elites",
            "fitness",
            "code",
            "sampling_weights",
            "regions",
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

    def regions(self) -> Sequence[RegionSummary]:
        """One region per island. `label` carries the native island naming the
        PES faithful prompt renders verbatim — it is supplied here, by the
        substrate that owns the topology, not synthesized by a coordinator."""
        return tuple(
            RegionSummary(
                scope=index,
                label=f"island_{index}",
                best_fitness=max(self.island_fitnesses(index), default=0.0),
                size=len(self._db.islands[index]),
            )
            for index in range(self.num_islands)
        )

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
            topology=self.topology,
            # Regional summaries are a global-perspective view: a local cohort
            # snapshot describes one region and does not enumerate its peers.
            regions=tuple(self.regions()) if scope is None else (),
        )

    def state_dict(self) -> Dict[str, Any]:
        return {"steps_per_generation": self.steps_per_generation}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.steps_per_generation = int(state.get("steps_per_generation", self.num_islands))
