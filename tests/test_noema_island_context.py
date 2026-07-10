"""
Tests for the cross-island context provider (task 0061).

The controller injects an `island_bests_provider` callable into the LOCAL
coordination-params copy; `SubstrateDatabase.per_island_bests()` supplies the
data; the PES planner renders it as an island-status block (consumed by the
faithful prompt variant, task 0063). The shared interface, the frozen run
config, and every existing prompt stay byte-identical.
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
from noema.config import BudgetConfig, LLMClientConfig, NoemaConfig
from noema.controller import NoemaController
from noema.coordination import build_coordination_module
from noema.coordination.base import GenerationContext, NullCoordination
from noema.coordination.pes.module import PESPlannerModule
from noema.substrate.database import SubstrateDatabase
from noema.substrate.views import ProgramView

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


def make_db(**overrides) -> SubstrateDatabase:
    defaults = dict(
        in_memory=True,
        num_islands=2,
        population_size=50,
        random_seed=42,
        migration_interval=1000,
    )
    defaults.update(overrides)
    return SubstrateDatabase(DatabaseConfig(**defaults))


def make_program(score=0.5, code="def f():\n    return 1\n") -> Program:
    return Program(
        id=str(uuid.uuid4()),
        code=code,
        language="python",
        metrics={"combined_score": score},
    )


def make_view(pid="p", fitness=0.5, code=INITIAL_PROGRAM) -> ProgramView:
    return ProgramView(id=pid, code=code, fitness=fitness, metrics={"score": fitness})


def make_ctx(parent=None) -> GenerationContext:
    return GenerationContext(
        iteration=0,
        generation=0,
        island=0,
        parent=parent or make_view(),
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
    def test_multi_island_with_empty_island(self):
        db = make_db(num_islands=3)
        db.add(make_program(score=0.4), target_island=0)
        db.add(make_program(score=0.7), target_island=0)
        db.add(make_program(score=0.2), target_island=1)
        # island 2 stays empty
        self.assertEqual(db.per_island_bests(), [0.7, 0.2, 0.0])

    def test_deterministic_for_same_db_state(self):
        db = make_db(num_islands=2)
        db.add(make_program(score=0.9), target_island=1)
        self.assertEqual(db.per_island_bests(), db.per_island_bests())


class TestIslandStatusBlock(unittest.TestCase):
    def test_formats_provider_values(self):
        module, _ = make_pes_module(island_bests_provider=lambda: [0.9812, 0.953])
        self.assertEqual(
            module._planner._island_status_block(),
            "Island status (best score per island): island_0: 0.9812, island_1: 0.9530",
        )

    def test_empty_without_provider_or_data(self):
        module, _ = make_pes_module()
        self.assertEqual(module._planner._island_status_block(), "")
        module, _ = make_pes_module(island_bests_provider=lambda: [])
        self.assertEqual(module._planner._island_status_block(), "")

    def test_deterministic_from_real_db(self):
        db = make_db(num_islands=2)
        db.add(make_program(score=0.6), target_island=0)
        module, _ = make_pes_module(island_bests_provider=db.per_island_bests)
        first = module._planner._island_status_block()
        self.assertEqual(first, module._planner._island_status_block())
        self.assertIn("island_0: 0.6000", first)

    def test_custom_planning_prompt_byte_identical_with_provider(self):
        # The custom prompt variant must not change whether or not the host
        # injected the provider (the faithful variant, task 0063, consumes it).
        with_provider, client_a = make_pes_module(
            island_bests_provider=lambda: [0.9812, 0.953]
        )
        without_provider, client_b = make_pes_module()
        asyncio.run(with_provider.advise(make_ctx()))
        asyncio.run(without_provider.advise(make_ctx()))
        self.assertEqual(client_a.calls[0]["messages"], client_b.calls[0]["messages"])


class TestOtherModulesIgnoreKey(unittest.TestCase):
    def test_null_and_hifo_ignore_unknown_key(self):
        params = {"island_bests_provider": lambda: [0.5]}
        null_module = NullCoordination(config=dict(params))
        advice = asyncio.run(null_module.advise(make_ctx()))
        self.assertEqual(advice.prompt_block, "")

        hifo_module = build_coordination_module("hifo", dict(params), llm=None)
        hifo_advice = asyncio.run(hifo_module.advise(make_ctx()))
        self.assertIsNotNone(hifo_advice)

if __name__ == "__main__":
    unittest.main()
