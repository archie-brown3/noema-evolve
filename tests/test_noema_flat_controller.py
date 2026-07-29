"""Flat-population behaviour through the real controller loop."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import BudgetConfig, NoemaConfig, SubstrateConfig
from noema.controller import NoemaController
from noema.coordination import NullCoordination


class _ArtifactEvaluator:
    async def evaluate_program(self, code, program_id):
        return {"combined_score": 0.9 if "return 9" in code else 0.1}

    def get_pending_artifacts(self, program_id):
        return {"stderr": f"artifact for {program_id}"}


def _mutation_client():
    async def create(**params):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="```python\ndef f():\n    return 1\n```"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class TestFlatPopulationController(unittest.TestCase):
    def test_valid_non_survivor_with_artifacts_does_not_abort_the_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            evaluation_file = os.path.join(tmp, "evaluator.py")
            with open(evaluation_file, "w") as handle:
                handle.write("def evaluate(program_path): return {'combined_score': 0.0}\n")
            ledger = TokenLedger(total_budget_tokens=10_000)
            config = NoemaConfig(
                max_iterations=1,
                checkpoint_interval=100,
                diff_based_evolution=False,
                database=DatabaseConfig(in_memory=True, population_size=1),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
                budget=BudgetConfig(total_tokens=10_000),
                substrate=SubstrateConfig(kind="flat"),
            )
            controller = NoemaController(
                config=config,
                evaluation_file=evaluation_file,
                initial_program_code="def f():\n    return 9\n",
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=BudgetedLLM(
                    model="fake",
                    ledger=ledger,
                    account="mutation",
                    tag="test",
                    client=_mutation_client(),
                    retries=0,
                    retry_delay=0.0,
                ),
                coordination=NullCoordination(),
                ledger=ledger,
            )
            controller.evaluator = _ArtifactEvaluator()

            asyncio.run(controller.run())

            self.assertEqual([program.id for program in controller.db.population()], ["initial"])
            self.assertEqual(controller.db._artifacts, {"initial": {"stderr": "artifact for initial"}})
