"""
Adapter around openevolve.database.ProgramDatabase.

Responsibilities:
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

from noema.evolution.views import ProgramView

logger = logging.getLogger(__name__)


class SubstrateDatabase:
    """Narrow, novelty-free wrapper over openevolve's ProgramDatabase"""

    def __init__(self, config: DatabaseConfig):
        # Hard-disable novelty checking: it makes embedding + LLM-judge calls
        # inside db.add() that would bypass the token ledger
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

    def top_programs(
        self, n: int, island: Optional[int] = None, metric: Optional[str] = None
    ) -> List[Program]:
        return self._db.get_top_programs(n, metric=metric, island_idx=island)

    def best_program(self, metric: Optional[str] = None) -> Optional[Program]:
        return self._db.get_best_program(metric=metric)

    def sample(self, num_inspirations: Optional[int] = None) -> Tuple[Program, List[Program]]:
        """Untargeted selection, off whichever island `current_island` names.

        Noema's own callers always target an island explicitly
        (`sample_from_island`); this exists because upstream's controller and
        test suite reach for it, and it carries upstream's shared-mutable-state
        behaviour verbatim rather than a corrected version of it.
        """
        return self._db.sample(num_inspirations=num_inspirations)

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

    def get_artifacts(self, program_id: str) -> Dict[str, Union[str, bytes]]:
        return self._db.get_artifacts(program_id)

    # -- upstream state, served live (0188 Stage 7) --------------------------
    #
    # Each property below returns the container openevolve itself owns, NOT a
    # copy or a rebuilt view. That is deliberate and it is the fidelity-relevant
    # choice, not a shortcut: these are read/WRITE surfaces (upstream's own
    # controller does `db.islands[i].add(id)`, `db.island_generations[i] += 1`),
    # and a rebuilt view would either absorb such a write silently or force this
    # wrapper to re-implement upstream's write semantics by hand. Handing back
    # the real container makes "reproduces upstream bug-for-bug" structural.
    # It also preserves upstream quirks a derived view cannot: island_best_programs
    # is maintained incrementally and may hold a STALE id after a program is
    # deleted, which is behaviour upstream's suite asserts.

    @property
    def programs(self) -> Dict[str, Program]:
        return self._db.programs

    @property
    def islands(self) -> List[set]:
        return self._db.islands

    @property
    def archive(self) -> set:
        return self._db.archive

    @property
    def feature_bins(self):
        return self._db.feature_bins

    @property
    def island_feature_maps(self) -> List[Dict[str, str]]:
        return self._db.island_feature_maps

    @property
    def island_generations(self) -> List[int]:
        return self._db.island_generations

    @island_generations.setter
    def island_generations(self, generations: List[int]) -> None:
        self._db.island_generations = generations

    @property
    def island_best_programs(self) -> List[Optional[str]]:
        return self._db.island_best_programs

    @property
    def last_migration_generation(self) -> int:
        return self._db.last_migration_generation

    @property
    def best_program_id(self) -> Optional[str]:
        return self._db.best_program_id

    @best_program_id.setter
    def best_program_id(self, program_id: Optional[str]) -> None:
        self._db.best_program_id = program_id

    @property
    def current_island(self) -> int:
        """Upstream's shared mutable selection target.

        Noema never advances it — `native_select`/`sample_from_island` take the
        island as an argument, which is why `IslandsStore` holds no
        current-island state of its own (upstream issue #246). This exposes
        upstream's field for callers that drive upstream's own controller; it is
        NOT Noema state, so it must stay a property and never appear in
        `vars(store)`.
        """
        return self._db.current_island

    @current_island.setter
    def current_island(self, island: int) -> None:
        self._db.current_island = island

    def set_current_island(self, island: int) -> None:
        self._db.set_current_island(island)

    def next_island(self) -> int:
        return self._db.next_island()

    def increment_island_generation(self, island: Optional[int] = None) -> None:
        self._db.increment_island_generation(island_idx=island)

    def should_migrate(self) -> bool:
        return self._db.should_migrate()

    def migrate_programs(self) -> None:
        self._db.migrate_programs()

    def validate_migration_results(self) -> None:
        self._db._validate_migration_results()

    def log_island_status(self) -> None:
        self._db.log_island_status()

    # -- upstream binning/diversity helpers, served under public names -------
    #
    # Upstream spells these with a leading underscore. Re-exporting that name
    # would claim upstream's private contract as Noema's; these are Noema public
    # API delegating to it, so they carry Noema names.

    def feature_coords(self, program: Program) -> List[int]:
        return self._db._calculate_feature_coords(program)

    def feature_coords_to_key(self, coords: List[int]) -> str:
        return self._db._feature_coords_to_key(coords)

    def complexity_bin(self, complexity: int) -> int:
        return self._db._calculate_complexity_bin(complexity)

    def diversity_bin(self, diversity: float) -> int:
        return self._db._calculate_diversity_bin(diversity)

    def code_diversity(self, code1: str, code2: str) -> float:
        return self._db._fast_code_diversity(code1, code2)

    def sample_inspirations(self, parent: Program, n: int = 5) -> List[Program]:
        return self._db._sample_inspirations(parent, n=n)

    def end_generation(self) -> bool:
        """
        Generation bookkeeping the external controller must drive:
        advance island generation counters and migrate when due.

        Returns True if a migration happened.
        """
        # One noema generation is a full round-robin sweep that touched every
        # island, so every island's counter advances. openevolve's own controller
        # does the same thing incrementally, passing the island explicitly per
        # iteration (process_parallel.py: increment_island_generation(island_idx=
        # island_id)). Calling it bare would default to _db.current_island, which
        # noema never advances — leaving [G, 0, 0, ...] instead of [G, G, G, ...].
        for scope in range(self.num_islands):
            self._db.increment_island_generation(island_idx=scope)
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
