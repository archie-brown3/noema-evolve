"""Cross-substrate portability of every coordination module (task 0080).

The claim under test: a coordination module consumes only the neutral interface
— immutable snapshots, declared capabilities, pre-selection hints — and so can
be composed with any population store. Until now that claim rested on a single
concrete store (islands), which is not evidence of anything.

Each module is therefore driven twice, through `IslandsStore` and through the
non-island `FixtureCVTStore`, from the *same* semantic roles. What must hold:

- the module constructs and runs against both, importing neither;
- substrate identity alone never changes an arm's behaviour, EXCEPT where the
  arm has a declared, logged topology adaptation (PES's region-worded database
  block) — which is the pre-registered mechanism x substrate interaction, not a
  silent drift;
- no module reaches for island-shaped state (num_islands, sample_from_island,
  a store handle, a callable) — it cannot, because it is never given one.
"""

import asyncio
import inspect
import random
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.coordination import build_coordination_module
from noema.coordination.base import (
    CoordinationModule,
    GenerationContext,
    NullCoordination,
    SelectionContext,
)
from noema.islands import IslandsStore
from tests.fixture_substrates import FixtureCVTStore, seed_store

SCORES = {0: [0.30, 0.55, 0.71], 1: [0.42, 0.68], 2: [0.10, 0.25, 0.60]}

MODULE_KEYS = ["null", "hifo", "pes-faithful"]


def make_islands_store() -> IslandsStore:
    store = IslandsStore(
        DatabaseConfig(
            in_memory=True,
            num_islands=3,
            population_size=50,
            random_seed=42,
            migration_interval=1000,
        )
    )
    seed_store(store, SCORES)
    return store


def make_cvt_store() -> FixtureCVTStore:
    store = FixtureCVTStore(num_regions=3, cells_per_region=4)
    seed_store(store, SCORES)
    return store


def make_ctx(store, scope=0) -> GenerationContext:
    """The same semantic roles, sourced from whichever substrate is in play."""
    selection = store.native_select(scope, num_inspirations=2)
    return GenerationContext(
        iteration=3,
        generation=1,
        scope_id=scope,
        parent=store.view(selection.parent),
        inspirations=store.views(selection.inspirations),
        local_population=store.snapshot(scope, limit=5),
        global_population=store.snapshot(None, limit=5),
        best_fitness_history=[0.5, 0.6],
        avg_fitness_history=[0.3, 0.4],
        diversity_history=[0.9, 0.8],
    )


def make_fake_llm():
    """A metered LLM whose client is fake — PES short-circuits on llm=None, so
    without this its advise() path would never render a prompt and the
    portability claim for the faithful arm would be vacuous."""
    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="## Plan Outline 1\nA\n"
                        "### Final Child Solution Generation Plan\nDo the thing."
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )

    llm = BudgetedLLM(
        model="fake-model",
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        account="coordination",
        tag="portability.coordination",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        retries=0,
        retry_delay=0.0,
    )
    return llm, calls


def build(key, llm=None):
    return build_coordination_module(key, {}, llm=llm, rng=random.Random(0))


class TestModulesRunOnBothSubstrates(unittest.TestCase):
    def test_every_module_advises_on_islands_and_on_a_non_island_store(self):
        for key in MODULE_KEYS:
            for store in (make_islands_store(), make_cvt_store()):
                with self.subTest(module=key, topology=store.topology):
                    module = build(key)
                    advice = asyncio.run(module.advise(make_ctx(store)))
                    self.assertIsNotNone(advice)
                    asyncio.run(module.on_generation_end(make_ctx(store)))
                    module.report_result(
                        make_ctx(store), None, dict(advice.attribution), True
                    )
                    # Checkpoint contract holds on both.
                    module.load_state_dict(module.state_dict())

    def test_pre_selection_hook_is_neutral_on_both_substrates(self):
        for key in MODULE_KEYS:
            for store in (make_islands_store(), make_cvt_store()):
                with self.subTest(module=key, topology=store.topology):
                    ctx = SelectionContext(
                        iteration=3,
                        generation=1,
                        scope_id=0,
                        local_population=store.snapshot(0),
                        global_population=store.snapshot(None),
                    )
                    request = build(key).sampling_request(ctx)
                    self.assertIsInstance(dict(request.hints), dict)


class TestSubstrateIdentityAloneChangesNothing(unittest.TestCase):
    """A substrate swap must not change an arm unless the arm declares it."""

    def test_null_and_hifo_are_identical_across_topologies(self):
        # Same fitnesses, same parent, same roles -> same prompt block. Any
        # difference here would be an undeclared substrate dependency.
        for key in ("null", "hifo"):
            with self.subTest(module=key):
                island_advice = asyncio.run(build(key).advise(make_ctx(make_islands_store())))
                cvt_advice = asyncio.run(build(key).advise(make_ctx(make_cvt_store())))
                self.assertEqual(island_advice.prompt_block, cvt_advice.prompt_block)
                self.assertEqual(island_advice.system_block, cvt_advice.system_block)
                self.assertNotIn("topology_adaptation", island_advice.attribution)
                self.assertNotIn("topology_adaptation", cvt_advice.attribution)

    def test_hifo_consumes_the_local_cohort_as_a_distribution(self):
        # HiFo's credit assignment reads local_population.fitnesses. That is
        # neutral in *type* on any substrate, but it carries a real statistical
        # requirement: the local scope must hold a population, not one elite.
        # This is the concrete reason a region (a group of cells) is the neutral
        # unit rather than a CVT cell — a cell-scoped cohort would silently
        # degenerate this to length 1.
        for store in (make_islands_store(), make_cvt_store()):
            with self.subTest(topology=store.topology):
                cohort = store.snapshot(0).fitnesses
                self.assertGreater(
                    len(cohort), 1, "a local cohort must be a population, not an elite"
                )


class TestNoConcreteSubstrateReachesCoordination(unittest.TestCase):
    def test_modules_never_import_a_concrete_store(self):
        import noema.coordination.hifo.module as hifo_mod
        import noema.coordination.pes.planner as pes_planner

        for module in (hifo_mod, pes_planner):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                self.assertNotIn("from noema.islands", source)
                self.assertNotIn("IslandsStore", source)
                self.assertNotIn("sample_from_island", source)
                self.assertNotIn("num_islands", source)

    def test_context_hands_over_no_store_and_no_callable(self):
        ctx = make_ctx(make_cvt_store())
        for value in vars(ctx).values():
            self.assertFalse(
                callable(value), "coordination receives data, never a live handle"
            )
        # And the snapshot is immutable.
        with self.assertRaises(Exception):
            ctx.global_population.regions = ()


class TestPESDeclaresItsTopologyAdaptation(unittest.TestCase):
    def test_faithful_planner_renders_native_labels_per_substrate(self):
        islands = make_islands_store()
        cvt = make_cvt_store()
        planner = build("pes-faithful")._planner

        island_block = planner._database_block(make_ctx(islands))
        self.assertIn("island_0: 0.7100", island_block)
        self.assertIn("includes 3 islands", island_block)

        cvt_block = planner._database_block(make_ctx(cvt))
        self.assertIn("region_0: 0.7100", cvt_block)
        self.assertIn("includes 3 regions", cvt_block)
        self.assertNotIn("island", cvt_block)

    def test_adaptation_is_declared_only_off_islands(self):
        planner = build("pes-faithful")._planner
        self.assertIsNone(planner.topology_adaptation(make_ctx(make_islands_store())))
        self.assertEqual(
            planner.topology_adaptation(make_ctx(make_cvt_store())),
            "region_worded_database_block:cvt_regions",
        )

    def test_faithful_arm_runs_end_to_end_on_a_non_island_store(self):
        # The strong form of the portability claim: the LoongFlow-derived arm,
        # written entirely against islands, completes a real advise() cycle on a
        # store it has never seen — no code change, no store handle — and the one
        # thing that differs (the topology wording) is declared, not silent.
        for store in (make_islands_store(), make_cvt_store()):
            with self.subTest(topology=store.topology):
                llm, calls = make_fake_llm()
                module = build("pes-faithful", llm=llm)
                advice = asyncio.run(module.advise(make_ctx(store)))

                self.assertTrue(advice.prompt_block, "the arm must produce a plan")
                self.assertIn("Do the thing.", advice.prompt_block)
                planning_prompt = calls[0]["messages"][1]["content"]
                self.assertIn("# Database", planning_prompt)

                if store.topology == "islands":
                    self.assertIn("includes 3 islands", planning_prompt)
                    self.assertNotIn("topology_adaptation", advice.attribution)
                else:
                    self.assertIn("includes 3 regions", planning_prompt)
                    self.assertNotIn("island", planning_prompt)
                    self.assertEqual(
                        advice.attribution["topology_adaptation"],
                        "region_worded_database_block:cvt_regions",
                    )


class TestTheFixtureIsActuallyDifferent(unittest.TestCase):
    """Guard the guard: a fixture that quietly behaves like islands proves nothing."""

    def test_topologies_disagree_on_labels_and_cadence(self):
        islands, cvt = make_islands_store(), make_cvt_store()
        self.assertNotEqual(islands.topology, cvt.topology)
        self.assertNotEqual(
            [r.label for r in islands.regions()], [r.label for r in cvt.regions()]
        )
        # Islands: scope = iteration % num_islands. The fixture deliberately
        # schedules differently, so an arm that hardcoded the island rule breaks.
        self.assertNotEqual(
            [islands.target_scope(i) for i in range(6)],
            [cvt.target_scope(i) for i in range(6)],
        )

    def test_the_fixture_never_leaks_into_production_code(self):
        import noema

        self.assertNotIn("fixture_substrates", str(vars(noema).keys()))


class TestCoordinationBaseStaysAbstract(unittest.TestCase):
    def test_null_is_a_coordination_module_with_no_substrate_knowledge(self):
        self.assertTrue(issubclass(NullCoordination, CoordinationModule))
        source = inspect.getsource(NullCoordination)
        self.assertNotIn("island", source)


if __name__ == "__main__":
    unittest.main()
