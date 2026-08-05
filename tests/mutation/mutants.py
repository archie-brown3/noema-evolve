"""0188 Stage 5 — the mutant catalogue (Option C: 40 mutants).

One wrong-but-well-formed mutant per public member of the two wrapper classes,
plus the declared-deviation novelty guard, plus the PromptSampler routing
surface. "Well-formed" = type-correct return, no exception on the happy path,
plausibly passes a weakly-written test. Grounding: vault note
"0188 OpenEvolve Fidelity Spec §5 — Mutation Matrix — 2026-08-05" §2a/§2b/§2c/§4a.

Applied by ``tests/mutation/plugin.py`` at ``pytest_configure``, one per process,
via ``setattr`` on the class (or module) object. No production file is edited and
the plugin is only loaded by an explicit ``-p tests.mutation.plugin``.
"""

import logging
import sys

from noema.evolution import prompts as _prompts
from noema.substrates.database import SubstrateDatabase
from noema.substrates.islands import IslandsStore

logger = logging.getLogger(__name__)


def _rebind_prompt_function(name, replacement):
    """Rebind a ``noema.evolution.prompts`` function everywhere it is already bound.

    ``noema/controller.py``, ``noema/agenthost/session.py`` and
    ``noema/evolution/iteration_runner.py`` do ``from ... import make_prompt_sampler``
    etc., so a setattr on the defining module alone would leave the production
    call sites on the original. Sweep sys.modules for the old object too.
    """
    original = getattr(_prompts, name)
    setattr(_prompts, name, replacement)
    for module in list(sys.modules.values()):
        if module is None or module is _prompts:
            continue
        try:
            if getattr(module, name, None) is original:
                setattr(module, name, replacement)
        except Exception:  # module with a hostile __getattr__; not our problem
            continue


# ---------------------------------------------------------------------------
# §2a — IslandsStore (17)
# ---------------------------------------------------------------------------


def _islands_topology_flat():
    IslandsStore.topology = "flat"


def _islands_capabilities_overclaim():
    IslandsStore.capabilities = IslandsStore.capabilities | {"behavior_descriptors"}


def _islands_steps_per_generation_one():
    original = IslandsStore.__init__

    def __init__(self, config):
        original(self, config)
        self.steps_per_generation = 1

    IslandsStore.__init__ = __init__


def _islands_scopes_drop_last():
    # max(1, ...) keeps the mutant well-formed at num_islands == 1 (an empty
    # scope tuple would crash rather than answer wrongly). Vacuous only there;
    # every fidelity fixture uses num_islands >= 2.
    IslandsStore.scopes = property(lambda self: tuple(range(max(1, self.num_islands - 1))))


def _islands_target_scope_off_by_one():
    IslandsStore.target_scope = lambda self, iteration: (iteration + 1) % self.num_islands


def _islands_population_ignore_scope():
    def population(self, scope=None):
        return tuple(self._db.programs[pid] for pid in sorted(self._db.programs))

    IslandsStore.population = population


def _islands_elites_worst_first():
    def elites(self, scope=None):
        candidates = self.population(scope)
        return tuple(sorted(candidates, key=self.fitness, reverse=False)[:10])

    IslandsStore.elites = elites


def _islands_top_programs_reversed():
    def top_programs(self, n, scope=None, island=None):
        selected_scope = scope if scope is not None else island
        return tuple(reversed(SubstrateDatabase.top_programs(self, n, island=selected_scope)))

    IslandsStore.top_programs = top_programs


def _islands_top_programs_island_wins():
    def top_programs(self, n, scope=None, island=None):
        selected_scope = island if island is not None else scope
        return SubstrateDatabase.top_programs(self, n, island=selected_scope)

    IslandsStore.top_programs = top_programs


def _islands_per_scope_bests_global_best():
    IslandsStore.per_scope_bests = lambda self: [
        max(self.all_fitnesses(), default=0.0)
    ] * self.num_islands


def _islands_regions_generic_label():
    from noema.substrates.base import RegionSummary

    def regions(self):
        return tuple(
            RegionSummary(
                scope=index,
                label=f"region_{index}",
                best_fitness=max(self.island_fitnesses(index), default=0.0),
                size=len(self._db.islands[index]),
            )
            for index in range(self.num_islands)
        )

    IslandsStore.regions = regions


def _islands_native_select_source_is_target():
    from noema.substrates.base import Selection

    def native_select(self, target_scope, num_inspirations):
        target = int(target_scope) % self.num_islands
        parent, inspirations = self._db.sample_from_island(
            target, num_inspirations=num_inspirations
        )
        return Selection(parent, tuple(inspirations), target, target)

    IslandsStore.native_select = native_select


def _islands_add_ignore_target_scope():
    def add(self, program, iteration=None, target_scope=None, target_island=None):
        return SubstrateDatabase.add(self, program, iteration=iteration, target_island=target_island)

    IslandsStore.add = add


def _islands_snapshot_regions_always():
    from noema.substrates.base import PopulationSnapshot

    def snapshot(self, scope=None, limit=None):
        programs = list(self.population(scope))
        programs.sort(key=self.fitness, reverse=True)
        if limit is not None:
            programs = programs[:limit]
        views = tuple(self.views(programs))
        return PopulationSnapshot(
            scope=scope,
            top_programs=views,
            fitnesses=tuple(self.fitness(p) for p in self.population(scope)),
            best_program=views[0] if views else None,
            topology=self.topology,
            regions=tuple(self.regions()),
        )

    IslandsStore.snapshot = snapshot


def _islands_snapshot_limit_before_sort():
    from noema.substrates.base import PopulationSnapshot

    def snapshot(self, scope=None, limit=None):
        programs = list(self.population(scope))
        if limit is not None:
            programs = programs[:limit]
        programs.sort(key=self.fitness, reverse=True)
        views = tuple(self.views(programs))
        return PopulationSnapshot(
            scope=scope,
            top_programs=views,
            fitnesses=tuple(self.fitness(p) for p in self.population(scope)),
            best_program=views[0] if views else None,
            topology=self.topology,
            regions=tuple(self.regions()) if scope is None else (),
        )

    IslandsStore.snapshot = snapshot


def _islands_state_dict_empty():
    IslandsStore.state_dict = lambda self: {}


def _islands_load_state_dict_ignore_state():
    def load_state_dict(self, state):
        self.steps_per_generation = self.num_islands

    IslandsStore.load_state_dict = load_state_dict


# ---------------------------------------------------------------------------
# §2b — SubstrateDatabase (16)
# ---------------------------------------------------------------------------


def _database_num_islands_off_by_one():
    SubstrateDatabase.num_islands = property(lambda self: max(1, len(self._db.islands) - 1))


def _database_feature_dimensions_empty():
    SubstrateDatabase.feature_dimensions = property(lambda self: [])


def _database_num_programs_off_by_one():
    SubstrateDatabase.num_programs = property(lambda self: max(0, len(self._db.programs) - 1))


def _database_get_best_fallback():
    SubstrateDatabase.get = lambda self, program_id: (
        self._db.get(program_id) or self._db.get_best_program()
    )


def _database_sample_from_island_drop_inspirations():
    SubstrateDatabase.sample_from_island = lambda self, island, num_inspirations: (
        self._db.sample_from_island(island)
    )


def _database_best_program_island_zero():
    def best_program(self):
        top = self._db.get_top_programs(1, island_idx=0)
        return top[0] if top else None

    SubstrateDatabase.best_program = best_program


def _database_fitness_no_feature_exclusion():
    from openevolve.utils.metrics_utils import get_fitness_score

    SubstrateDatabase.fitness = lambda self, program: get_fitness_score(program.metrics, None)


def _database_island_fitnesses_global():
    def island_fitnesses(self, island):
        return self.all_fitnesses()

    SubstrateDatabase.island_fitnesses = island_fitnesses


def _database_per_island_bests_neg_inf_default():
    SubstrateDatabase.per_island_bests = lambda self: [
        max(self.island_fitnesses(i), default=float("-inf")) for i in range(self.num_islands)
    ]


def _database_all_fitnesses_island_zero():
    def all_fitnesses(self):
        return [
            self.fitness(self._db.programs[pid])
            for pid in self._db.islands[0]
            if pid in self._db.programs
        ]

    SubstrateDatabase.all_fitnesses = all_fitnesses


def _database_store_artifacts_noop():
    SubstrateDatabase.store_artifacts = lambda self, program_id, artifacts: None


def _database_end_generation_bare_increment():
    def end_generation(self):
        self._db.increment_island_generation()
        if self._db.should_migrate():
            self._db.migrate_programs()
            return True
        return False

    SubstrateDatabase.end_generation = end_generation


def _database_end_generation_always_false():
    def end_generation(self):
        for scope in range(self.num_islands):
            self._db.increment_island_generation(island_idx=scope)
        if self._db.should_migrate():
            self._db.migrate_programs()
        return False

    SubstrateDatabase.end_generation = end_generation


def _database_save_iteration_zero():
    SubstrateDatabase.save = lambda self, path, iteration=0: self._db.save(path, 0)


def _database_load_reset_generations():
    def load(self, path):
        self._db.load(path)
        self._db.island_generations = [0] * self.num_islands

    SubstrateDatabase.load = load


def _database_last_iteration_off_by_one():
    SubstrateDatabase.last_iteration = property(lambda self: self._db.last_iteration + 1)


# ---------------------------------------------------------------------------
# §2c — X1, the declared-deviation novelty guard (1)
# ---------------------------------------------------------------------------


def _database_init_no_novelty_guard():
    from openevolve.database import ProgramDatabase

    def __init__(self, config):
        if config.embedding_model is not None or config.novelty_llm is not None:
            logger.warning("novelty features enabled; their LLM calls bypass the token ledger")
        self.config = config
        self._db = ProgramDatabase(config)

    SubstrateDatabase.__init__ = __init__


# ---------------------------------------------------------------------------
# §4a — PromptSampler routing (6)
# ---------------------------------------------------------------------------


def _prompts_make_prompt_sampler_allow_stochasticity():
    from openevolve.config import PromptConfig
    from openevolve.prompt.sampler import PromptSampler

    from noema.evolution.operators import OPERATOR_TEMPLATES

    def make_prompt_sampler(config=None):
        if config is None:
            config = PromptConfig()
        if config.use_template_stochasticity:
            logger.warning("prompt.use_template_stochasticity=True")
        sampler = PromptSampler(config)
        for key, text in OPERATOR_TEMPLATES.items():
            sampler.template_manager.add_template(key, text)
        return sampler

    _rebind_prompt_function("make_prompt_sampler", make_prompt_sampler)


def _prompts_make_prompt_sampler_skip_template_registration():
    from openevolve.config import PromptConfig
    from openevolve.prompt.sampler import PromptSampler

    def make_prompt_sampler(config=None):
        if config is None:
            config = PromptConfig()
        if config.use_template_stochasticity:
            raise ValueError(
                "noema requires prompt.use_template_stochasticity=False; "
                "random phrase variations void the identical-prompts guarantee across arms"
            )
        return PromptSampler(config)

    _rebind_prompt_function("make_prompt_sampler", make_prompt_sampler)


def _build_mutation_prompt_variant(*, filter_metrics, filter_lists, keep_parent2):
    from noema.evolution.prompts import _filter_metrics, _filter_program_list

    def build_mutation_prompt(
        sampler,
        parent,
        top_programs,
        previous_programs,
        inspirations,
        language,
        iteration,
        diff_based_evolution,
        feature_dimensions,
        parent_artifacts=None,
        template_key=None,
        parent2=None,
        metric_fields=None,
    ):
        metrics = _filter_metrics(parent.metrics, metric_fields) if filter_metrics else parent.metrics
        previous = [p.to_dict() for p in previous_programs]
        top = [p.to_dict() for p in top_programs]
        if filter_lists:
            previous = _filter_program_list(previous, metric_fields)
            top = _filter_program_list(top, metric_fields)
        return sampler.build_prompt(
            current_program=parent.code,
            parent_program=parent.code,
            program_metrics=metrics,
            previous_programs=previous,
            top_programs=top,
            inspirations=[p.to_dict() for p in inspirations],
            language=language,
            evolution_round=iteration,
            diff_based_evolution=diff_based_evolution,
            program_artifacts=parent_artifacts if parent_artifacts else None,
            feature_dimensions=feature_dimensions,
            template_key=template_key,
            parent2_program=(parent2.code if parent2 else "") if keep_parent2 else "",
        )

    _rebind_prompt_function("build_mutation_prompt", build_mutation_prompt)


def _prompts_build_mutation_prompt_unfiltered_metrics():
    _build_mutation_prompt_variant(filter_metrics=False, filter_lists=True, keep_parent2=True)


def _prompts_build_mutation_prompt_unfiltered_program_lists():
    _build_mutation_prompt_variant(filter_metrics=True, filter_lists=False, keep_parent2=True)


def _prompts_build_mutation_prompt_drop_parent2():
    _build_mutation_prompt_variant(filter_metrics=True, filter_lists=True, keep_parent2=False)


def _prompts_inject_advice_no_header():
    from noema.evolution.prompts import SYSTEM_SUFFIX_SEPARATOR

    def inject_advice(prompt, prompt_block, system_block):
        system = prompt["system"]
        user = prompt["user"]
        if system_block:
            system = system + SYSTEM_SUFFIX_SEPARATOR + system_block
        if prompt_block:
            user = user + prompt_block
        return {"system": system, "user": user}

    _rebind_prompt_function("inject_advice", inject_advice)


MUTANTS = {
    # §2a IslandsStore
    "islands.topology.flat": _islands_topology_flat,
    "islands.capabilities.overclaim": _islands_capabilities_overclaim,
    "islands.steps_per_generation.one": _islands_steps_per_generation_one,
    "islands.scopes.drop_last": _islands_scopes_drop_last,
    "islands.target_scope.off_by_one": _islands_target_scope_off_by_one,
    "islands.population.ignore_scope": _islands_population_ignore_scope,
    "islands.elites.worst_first": _islands_elites_worst_first,
    "islands.top_programs.reversed": _islands_top_programs_reversed,
    "islands.top_programs.island_wins": _islands_top_programs_island_wins,
    "islands.per_scope_bests.global_best": _islands_per_scope_bests_global_best,
    "islands.regions.generic_label": _islands_regions_generic_label,
    "islands.native_select.source_is_target": _islands_native_select_source_is_target,
    "islands.add.ignore_target_scope": _islands_add_ignore_target_scope,
    "islands.snapshot.regions_always": _islands_snapshot_regions_always,
    "islands.snapshot.limit_before_sort": _islands_snapshot_limit_before_sort,
    "islands.state_dict.empty": _islands_state_dict_empty,
    "islands.load_state_dict.ignore_state": _islands_load_state_dict_ignore_state,
    # §2b SubstrateDatabase
    "database.num_islands.off_by_one": _database_num_islands_off_by_one,
    "database.feature_dimensions.empty": _database_feature_dimensions_empty,
    "database.num_programs.off_by_one": _database_num_programs_off_by_one,
    "database.get.best_fallback": _database_get_best_fallback,
    "database.sample_from_island.drop_inspirations": _database_sample_from_island_drop_inspirations,
    "database.best_program.island_zero": _database_best_program_island_zero,
    "database.fitness.no_feature_exclusion": _database_fitness_no_feature_exclusion,
    "database.island_fitnesses.global": _database_island_fitnesses_global,
    "database.per_island_bests.neg_inf_default": _database_per_island_bests_neg_inf_default,
    "database.all_fitnesses.island_zero": _database_all_fitnesses_island_zero,
    "database.store_artifacts.noop": _database_store_artifacts_noop,
    "database.end_generation.bare_increment": _database_end_generation_bare_increment,
    "database.end_generation.always_false": _database_end_generation_always_false,
    "database.save.iteration_zero": _database_save_iteration_zero,
    "database.load.reset_generations": _database_load_reset_generations,
    "database.last_iteration.off_by_one": _database_last_iteration_off_by_one,
    # §2c X1
    "database.init.no_novelty_guard": _database_init_no_novelty_guard,
    # §4a PromptSampler routing
    "prompts.make_prompt_sampler.allow_stochasticity": _prompts_make_prompt_sampler_allow_stochasticity,
    "prompts.make_prompt_sampler.skip_template_registration": _prompts_make_prompt_sampler_skip_template_registration,
    "prompts.build_mutation_prompt.unfiltered_metrics": _prompts_build_mutation_prompt_unfiltered_metrics,
    "prompts.build_mutation_prompt.unfiltered_program_lists": _prompts_build_mutation_prompt_unfiltered_program_lists,
    "prompts.build_mutation_prompt.drop_parent2": _prompts_build_mutation_prompt_drop_parent2,
    "prompts.inject_advice.no_header": _prompts_inject_advice_no_header,
}
