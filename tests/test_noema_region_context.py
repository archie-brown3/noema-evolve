"""
Tests for the neutral regional context (task 0061, migrated by task 0080).

Task 0061 gave the PES faithful planner its cross-island "Global Perspective"
data through an `island_bests_provider` callable that the controller injected
into the coordination params. That callable was a live handle into a concrete
store, so no coordination module could honestly claim to be substrate-neutral.

Task 0080 replaced it: the store publishes `RegionSummary` objects, they ride on
the immutable `global_population` snapshot, and the planner renders them. This
file therefore now asserts the *inverse* of what it used to — no callable ever
reaches a coordination module — while keeping every original guarantee about the
data itself (per-region bests are correct, deterministic, and rendered
byte-identically for the islands fidelity anchor).
"""

import asyncio
import hashlib
import os
import random
import tempfile
import unittest
import uuid
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig
from openevolve.database import Program

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import BudgetConfig, LLMClientConfig, LLMRolesConfig, NoemaConfig
from noema.controller import NoemaController
from noema.coordination import build_coordination_module
from noema.coordination.base import GenerationContext, NullCoordination
from noema.coordination.pes.module import PESPlannerModule
from noema.base import PopulationSnapshot, RegionSummary
from noema.islands import IslandsStore
from noema.views import ProgramView

INITIAL_PROGRAM = "def f():\n    return 1\n"

EVAL_SCRIPT = """\
import re

def evaluate(program_path):
    with open(program_path) as f:
        code = f.read()
    m = re.search(r"return (\\d+(?:\\.\\d+)?)", code)
    value = float(m.group(1)) if m else 0.0
    return {"combined_score": min(1.0, value / 10.0)}
"""


def make_store(**overrides) -> IslandsStore:
    defaults = dict(
        in_memory=True,
        num_islands=2,
        population_size=50,
        random_seed=42,
        migration_interval=1000,
    )
    defaults.update(overrides)
    return IslandsStore(DatabaseConfig(**defaults))


def make_program(score=0.5, code="def f():\n    return 1\n") -> Program:
    return Program(
        id=str(uuid.uuid4()),
        code=code,
        language="python",
        metrics={"combined_score": score},
    )


def make_view(pid="p", fitness=0.5, code=INITIAL_PROGRAM) -> ProgramView:
    return ProgramView(id=pid, code=code, fitness=fitness, metrics={"score": fitness})


def make_ctx(parent=None, regions=(), topology="islands") -> GenerationContext:
    return GenerationContext(
        iteration=0,
        generation=0,
        scope_id=0,
        parent=parent or make_view(),
        global_population=PopulationSnapshot(
            scope=None, topology=topology, regions=tuple(regions)
        ),
        best_fitness_history=[0.1, 0.2],
        avg_fitness_history=[0.05, 0.1],
    )


def make_plan_client(response_text="# Plan\n\n## Strategy\n- x"):
    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=80),
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls
    )


def make_pes_module(**params) -> PESPlannerModule:
    client = make_plan_client()
    llm = BudgetedLLM(
        model="fake-model",
        ledger=TokenLedger(total_budget_tokens=100_000),
        account="coordination",
        tag="pes.coordination",
        client=client,
        retries=0,
        retry_delay=0.0,
    )
    module = PESPlannerModule(config=params, llm=llm, rng=random.Random(0))
    return module, client


class TestPerIslandBests(unittest.TestCase):
    """The store-side data. Unchanged by 0080 — only its delivery route moved."""

    def test_multi_island_with_empty_island(self):
        store = make_store(num_islands=3)
        store.add(make_program(score=0.4), target_scope=0)
        store.add(make_program(score=0.7), target_scope=0)
        store.add(make_program(score=0.2), target_scope=1)
        # island 2 stays empty
        self.assertEqual(store.per_island_bests(), [0.7, 0.2, 0.0])

    def test_deterministic_for_same_db_state(self):
        store = make_store(num_islands=2)
        store.add(make_program(score=0.9), target_scope=1)
        self.assertEqual(store.per_island_bests(), store.per_island_bests())


class TestIslandsStoreRegions(unittest.TestCase):
    def test_one_region_per_island_with_native_labels(self):
        store = make_store(num_islands=3)
        store.add(make_program(score=0.7), target_scope=0)
        store.add(make_program(score=0.2), target_scope=1)
        regions = store.regions()
        self.assertEqual(
            [r.label for r in regions], ["island_0", "island_1", "island_2"]
        )
        self.assertEqual([r.best_fitness for r in regions], [0.7, 0.2, 0.0])
        self.assertEqual([r.scope for r in regions], [0, 1, 2])
        # Region bests and the legacy per-scope bests are the same numbers.
        self.assertEqual(
            [r.best_fitness for r in regions], list(store.per_scope_bests())
        )

    def test_regions_ride_on_the_global_snapshot_only(self):
        store = make_store(num_islands=2)
        store.add(make_program(score=0.7), target_scope=0)
        self.assertEqual(len(store.snapshot(None).regions), 2)
        self.assertEqual(store.snapshot(None).topology, "islands")
        # A local cohort snapshot describes one region; it does not enumerate
        # its peers (that would make "local" and "global" the same object).
        self.assertEqual(store.snapshot(0).regions, ())
        self.assertEqual(store.snapshot(0).topology, "islands")

    def test_store_declares_the_regions_capability(self):
        self.assertIn("regions", make_store().capabilities)


class TestDatabaseBlock(unittest.TestCase):
    def test_formats_region_values(self):
        module, _ = make_pes_module()
        block = module._planner._database_block(
            make_ctx(
                regions=(
                    RegionSummary(0, "island_0", 0.9812, 1),
                    RegionSummary(1, "island_1", 0.953, 1),
                )
            )
        )
        self.assertIn(
            "Island status (best score per island): island_0: 0.9812, island_1: 0.9530",
            block,
        )
        self.assertIn("The current database includes 2 islands", block)

    def test_status_absent_without_regions(self):
        module, _ = make_pes_module()
        self.assertNotIn("Island status", module._planner._database_block(make_ctx()))

    def test_deterministic_from_real_store(self):
        store = make_store(num_islands=2)
        store.add(make_program(score=0.6), target_scope=0)
        module, _ = make_pes_module()
        ctx = make_ctx(regions=store.snapshot(None).regions)
        first = module._planner._database_block(ctx)
        self.assertEqual(first, module._planner._database_block(ctx))
        self.assertIn("island_0: 0.6000", first)

    def test_custom_planning_prompt_byte_identical_with_and_without_regions(self):
        # The custom prompt variant must not change whether or not the substrate
        # publishes regions (only the faithful variant, task 0063, consumes them).
        with_regions, client_a = make_pes_module()
        without_regions, client_b = make_pes_module()
        asyncio.run(
            with_regions.advise(
                make_ctx(regions=(RegionSummary(0, "island_0", 0.98, 1),))
            )
        )
        asyncio.run(without_regions.advise(make_ctx()))
        self.assertEqual(client_a.calls[0]["messages"], client_b.calls[0]["messages"])


class TestOtherModulesIgnoreRegions(unittest.TestCase):
    def test_null_and_hifo_tolerate_a_region_bearing_snapshot(self):
        ctx = make_ctx(regions=(RegionSummary(0, "island_0", 0.5, 1),))
        advice = asyncio.run(NullCoordination().advise(ctx))
        self.assertEqual(advice.prompt_block, "")

        hifo_module = build_coordination_module("hifo", {}, llm=None)
        self.assertIsNotNone(asyncio.run(hifo_module.advise(ctx)))


class TestNoConcreteStoreCallbackReachesCoordination(unittest.TestCase):
    """Task 0080 acceptance criterion 1, as an executable assertion.

    This is the inverse of the test task 0061 shipped here. The old one proved
    the `island_bests_provider` callable *was* injected into the module's params;
    this one proves no such callable reaches a module at all, while keeping 0061's
    guarantee that the frozen run-config hash is unperturbed.
    """

    def test_coordination_params_hold_no_store_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = NoemaConfig(
                # the else-branch builds a real client object (no calls made)
                llm=LLMRolesConfig(
                    mutation=LLMClientConfig(api_key="test-key"),
                    coordination=LLMClientConfig(api_key="test-key"),
                ),
                database=DatabaseConfig(
                    in_memory=True,
                    num_islands=2,
                    population_size=50,
                    random_seed=42,
                    migration_interval=1000,
                ),
                evaluator=EvaluatorConfig(
                    cascade_evaluation=False, timeout=30, max_retries=0
                ),
                budget=BudgetConfig(total_tokens=1_000_000),
            )
            sha_before = hashlib.sha256(config.to_yaml().encode("utf-8")).hexdigest()

            mutation_llm = BudgetedLLM(
                model="fake-model",
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                account="mutation",
                tag="mutate",
                client=make_plan_client(),
                retries=0,
                retry_delay=0.0,
            )
            # No `coordination=` argument: the controller builds the module
            # itself and runs the params block under test.
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=mutation_llm,
            )

            params = controller.coordination.config
            self.assertNotIn("island_bests_provider", params)
            callables = {k: v for k, v in params.items() if callable(v)}
            self.assertEqual(
                callables,
                {},
                "a coordination module must not hold a live handle into the store",
            )
            # The data itself still reaches the module — on the snapshot.
            snapshot = controller.db.snapshot(None)
            self.assertEqual(
                [r.label for r in snapshot.regions], ["island_0", "island_1"]
            )
            self.assertEqual([r.best_fitness for r in snapshot.regions], [0.0, 0.0])

            # 0061's original guarantee, retained: nothing here perturbs the
            # frozen run config's hash.
            sha_after = hashlib.sha256(config.to_yaml().encode("utf-8")).hexdigest()
            self.assertEqual(sha_before, sha_after)


if __name__ == "__main__":
    unittest.main()
