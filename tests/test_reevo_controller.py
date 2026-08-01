"""Full-controller-loop coverage for the ReEvo coordination arm (task 0149).

Drives null vs ``reevo`` through a real ``NoemaController.run()`` to verify
prompt suffix identity, ledger accounting, advice attribution, paired-config
identity, and stateless checkpoint resume.
"""

from __future__ import annotations

import asyncio
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import CoordinationConfig, NoemaConfig
from noema.controller import NoemaController
from noema.coordination import NullCoordination, build_coordination_module
from noema.coordination.reevo.module import REFLECTION_TAG
from tests.test_noema_controller import EVAL_SCRIPT, INITIAL_PROGRAM, make_config

REFLECTION_TEXT = "improve the boundary rule"


def mutation_client():
    calls = []
    counter = [0]

    async def create(**params):
        calls.append(params)
        counter[0] += 1
        content = f"```python\ndef f():\n    return {counter[0] + 1}\n```"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40),
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    ), calls


def reevo_coordination_client(response=REFLECTION_TEXT):
    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    ), calls


def _build_controller(tmp, arm, iterations=6, coordination_client=None):
    eval_path = os.path.join(tmp, "evaluator.py")
    if not os.path.exists(eval_path):
        with open(eval_path, "w") as f:
            f.write(EVAL_SCRIPT)

    config = NoemaConfig(
        max_iterations=iterations,
        checkpoint_interval=100,
        diff_based_evolution=False,
        database=DatabaseConfig(
            in_memory=True,
            num_islands=2,
            population_size=50,
            random_seed=42,
            migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        coordination=CoordinationConfig(
            module="reevo" if arm == "reevo" else "null",
            params={
                "domain_context": "maximize packing quality",
                "function_name": "heuristic",
            },
        ),
    )

    ledger = TokenLedger(total_budget_tokens=1_000_000)
    mut_client, mut_calls = mutation_client()
    mutation_llm = BudgetedLLM(
        model="fake-model",
        ledger=ledger,
        account="mutation",
        tag="mutate",
        client=mut_client,
        retries=0,
        retry_delay=0.0,
    )

    if arm == "reevo":
        cc, coord_calls = coordination_client or reevo_coordination_client()
        coordination_llm = BudgetedLLM(
            model="fake-model",
            ledger=ledger,
            account="coordination",
            tag="reevo.coordination",
            client=cc,
            retries=0,
            retry_delay=0.0,
        )
        coordination = build_coordination_module(
            "reevo",
            config.coordination.params,
            llm=coordination_llm,
            rng=random.Random(43),
        )
        coordination._coord_calls = coord_calls  # type: ignore[attr-defined]
    else:
        coordination = NullCoordination()
        coordination._coord_calls = []  # type: ignore[attr-defined]

    controller = NoemaController(
        config=config,
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, f"output_{arm}"),
        mutation_llm=mutation_llm,
        coordination=coordination,
        ledger=ledger,
    )
    return controller, ledger, mut_calls, coordination


class TestReEvoController(unittest.TestCase):
    def test_reflection_suffix_on_null_prefix(self):
        with tempfile.TemporaryDirectory() as tmp_off, tempfile.TemporaryDirectory() as tmp_on:
            null_c, ledger_off, mut_off, _ = _build_controller(tmp_off, "null")
            reevo_c, ledger_on, mut_on, _ = _build_controller(tmp_on, "reevo")
            asyncio.run(null_c.run())
            asyncio.run(reevo_c.run())

            self.assertEqual(len(mut_off), len(mut_on))
            self.assertEqual(ledger_off.spent("coordination"), 0)
            self.assertGreater(ledger_on.spent("coordination"), 0)
            self.assertEqual(ledger_off.spent("mutation"), ledger_on.spent("mutation"))

            reflection_prompts = [
                call["messages"][-1]["content"]
                for call in mut_on
                if "[Reflection]" in call["messages"][-1]["content"]
            ]
            self.assertTrue(reflection_prompts, "reevo never injected a reflection")
            for user_on in reflection_prompts:
                self.assertIn(REFLECTION_TEXT, user_on)
            for call in mut_off:
                self.assertNotIn("[Reflection]", call["messages"][-1]["content"])

    def test_reflection_tag_and_prompt_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, _, coordination = _build_controller(tmp, "reevo", iterations=6)
            asyncio.run(controller.run())
            coord_calls = coordination._coord_calls  # type: ignore[attr-defined]
            self.assertTrue(coord_calls)
            reflection_tags = [
                record.tag
                for record in ledger.records
                if record.account == "coordination"
            ]
            self.assertIn(REFLECTION_TAG, reflection_tags)
            user_prompt = coord_calls[0]["messages"][-1]["content"]
            self.assertIn("[Worse code]", user_prompt)
            self.assertIn("[Better code]", user_prompt)
            self.assertIn("less than 20 words", user_prompt)

    def test_attribution_reaches_child_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = _build_controller(tmp, "reevo", iterations=6)
            asyncio.run(controller.run())
            children = [
                p for p in controller.db._db.programs.values() if p.parent_id is not None
            ]
            self.assertTrue(children)
            attributed = [
                child
                for child in children
                if child.metadata.get("coordination", {}).get("reevo", {}).get("status")
                == "generated"
            ]
            self.assertTrue(attributed, "expected at least one generated reflection attribution")
            meta = attributed[0].metadata["coordination"]["reevo"]
            for key in ("parent_id", "better_id", "topology", "donor_commit"):
                self.assertIn(key, meta)

    def test_skips_when_no_fitter_exemplar(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, _, _ = _build_controller(tmp, "reevo", iterations=1)
            asyncio.run(controller.run())
            self.assertEqual(ledger.spent("coordination"), 0)

    def test_paired_config_differs_only_in_module(self):
        null_config = make_config(coordination=CoordinationConfig(module="null"))
        reevo_config = make_config(coordination=CoordinationConfig(module="reevo"))
        null_lines = null_config.to_yaml().splitlines()
        reevo_lines = reevo_config.to_yaml().splitlines()
        self.assertEqual(len(null_lines), len(reevo_lines))
        differing = [(a, b) for a, b in zip(null_lines, reevo_lines) if a != b]
        self.assertTrue(differing)
        for a, b in differing:
            self.assertIn("module", a)
            self.assertIn("module", b)

    def test_checkpoint_resume_stateless(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, coordination = _build_controller(tmp, "reevo", iterations=6)
            asyncio.run(controller._ensure_initial_program())
            first_half = 3
            asyncio.run(controller.run(iterations=first_half))
            self.assertEqual(coordination.state_dict(), {})
            checkpoint = controller.save_checkpoint(first_half)

            controller2, _, _, coordination2 = _build_controller(tmp, "reevo", iterations=6)
            controller2.load_checkpoint(checkpoint)
            self.assertEqual(coordination2.state_dict(), {})
            asyncio.run(controller2.run(iterations=3))
            self.assertGreater(controller2.db.num_programs, 1)


if __name__ == "__main__":
    unittest.main()
