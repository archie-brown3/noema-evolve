"""
Adapter around openevolve.database.ProgramDatabase.

Responsibilities (PLAN.md sections 1.2 and 1.4):
- construct the database with the novelty features hard-disabled, so db.add()
  can never make embedding/LLM-judge network calls behind the ledger's back;
- own the island/migration bookkeeping that openevolve's own controller drives
  (increment_island_generation, should_migrate, migrate_programs);
- expose the narrow read API the noema controller and coordination modules use.
"""

import logging
from typing import Dict, List, Optional, Tuple, Union

from openevolve.config import DatabaseConfig
from openevolve.database import Program, ProgramDatabase
from openevolve.utils.metrics_utils import get_fitness_score

from noema.substrate.views import ProgramView

logger = logging.getLogger(__name__)


class SubstrateDatabase:
    """Narrow, novelty-free wrapper over openevolve's ProgramDatabase"""

    def __init__(self, config: DatabaseConfig):
        # Hard-disable novelty checking: it makes embedding + LLM-judge calls
        # inside db.add() that would bypass the token ledger (PLAN.md risk 2)
        if config.embedding_model is not None or config.novelty_llm is not None:
            raise ValueError(
                "noema requires novelty features disabled "
                "(database.embedding_model and database.novelty_llm must be None); "
                "their LLM calls would bypass the token ledger"
            )
        self.config = config
        self._db = ProgramDatabase(config)

    @property
    def num_islands(self) -> int:
        return len(self._db.islands)

    @property
    def feature_dimensions(self) -> List[str]:
        return self._db.config.feature_dimensions

    @property
    def num_programs(self) -> int:
        return len(self._db.programs)

    def add(
        self, program: Program, iteration: Optional[int] = None, target_island: Optional[int] = None
    ) -> str:
        return self._db.add(program, iteration=iteration, target_island=target_island)

    def get(self, program_id: str) -> Optional[Program]:
        return self._db.get(program_id)

    def sample_from_island(
        self, island: int, num_inspirations: int
    ) -> Tuple[Program, List[Program]]:
        return self._db.sample_from_island(island, num_inspirations=num_inspirations)

    def top_programs(self, n: int, island: Optional[int] = None) -> List[Program]:
        return self._db.get_top_programs(n, island_idx=island)

    def best_program(self) -> Optional[Program]:
        return self._db.get_best_program()

    def fitness(self, program: Program) -> float:
        """Scalar fitness under noema's fixed convention (maximized)"""
        return get_fitness_score(program.metrics, self.feature_dimensions)

    def island_fitnesses(self, island: int) -> List[float]:
        """All fitness values on one island (population statistics for coordination)"""
        island = island % self.num_islands
        return [
            self.fitness(self._db.programs[pid])
            for pid in self._db.islands[island]
            if pid in self._db.programs
        ]

    def per_island_bests(self) -> List[float]:
        """Best fitness on each island (0.0 for an empty island) — the
        cross-island status level the PES planner's Global Perspective
        strategies compare against (task 0061)."""
        return [
            max(self.island_fitnesses(i), default=0.0) for i in range(self.num_islands)
        ]

    def all_fitnesses(self) -> List[float]:
        """Fitness values of every program in the database (for host histories)"""
        return [self.fitness(p) for p in self._db.programs.values()]

    def view(self, program: Program) -> ProgramView:
        return ProgramView.from_program(program, self.feature_dimensions)

    def views(self, programs: List[Program]) -> List[ProgramView]:
        return [self.view(p) for p in programs]

    def store_artifacts(self, program_id: str, artifacts: Dict[str, Union[str, bytes]]) -> None:
        self._db.store_artifacts(program_id, artifacts)

    def end_generation(self) -> bool:
        """
        Generation bookkeeping the external controller must drive (PLAN.md 1.2):
        advance island generation counters and migrate when due.

        Returns True if a migration happened.
        """
        self._db.increment_island_generation()
        if self._db.should_migrate():
            logger.info("Migration due — migrating programs between islands")
            self._db.migrate_programs()
            return True
        return False

    def save(self, path: str, iteration: int = 0) -> None:
        self._db.save(path, iteration)

    def load(self, path: str) -> None:
        self._db.load(path)

    @property
    def last_iteration(self) -> int:
        return self._db.last_iteration
