"""
Tests for the PES planner arm (noema.coordination.pes).

Mirrors the HiFo test discipline: fake chat client, ledger assertions, and
hand-computed outcome classifications. The reflective summarizer is Phase 2
and intentionally untested here.
"""

import asyncio
import json
import random
import unittest
from types import SimpleNamespace

from noema.budget.ledger import BudgetExhausted, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.coordination import build_coordination_module
from noema.coordination.base import GenerationContext
from noema.coordination.pes.module import (
    FAILED,
    IMPROVED,
    PESPlannerModule,
    REGRESSED,
    STALE,
)
from noema.substrate.views import ProgramView

PLAN_TEXT = """# Plan

## Situation Analysis
- Quicksort degrades on nearly-sorted input

## Strategy
- Switch to insertion sort for small partitions

## Action Steps
1. Add a partition-size threshold

## Success Criteria
- avg_time improves on nearly-sorted benchmark"""


def make_view(pid="p", fitness=0.5, code="def f():\n    return 1\n") -> ProgramView:
    return ProgramView(id=pid, code=code, fitness=fitness, metrics={"score": fitness})


def make_ctx(**overrides) -> GenerationContext:
    defaults = dict(
        iteration=0,
        generation=0,
        island=0,
        parent=make_view(),
        best_fitness_history=[0.1, 0.2],
        avg_fitness_history=[0.05, 0.1],
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


def make_plan_client(response_text=PLAN_TEXT, fail_with=None):
    """Fake AsyncOpenAI returning a fixed plan (or raising)"""
    calls = []

    async def create(**params):
        calls.append(params)
        if fail_with is not None:
            raise fail_with
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=80),
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls
    )
    return client


class TestPESPlannerModule(unittest.TestCase):
    def make_module(self, response=PLAN_TEXT, fail_with=None, budget=100_000, **params):
        ledger = TokenLedger(total_budget_tokens=budget)
        client = make_plan_client(response, fail_with=fail_with)
        llm = BudgetedLLM(
            model="fake-model",
            ledger=ledger,
            account="coordination",
            tag="pes.coordination",
            client=client,
            retries=0,
            retry_delay=0.0,
        )
        module = PESPlannerModule(config=params, llm=llm, rng=random.Random(0))
        return module, ledger, client

    # ------------------------------------------------------------- advise

    def test_plan_reaches_prompt_block_and_charges_coordination(self):
        module, ledger, client = self.make_module()
        advice = asyncio.run(module.advise(make_ctx()))

        self.assertEqual(advice.prompt_block, PLAN_TEXT)
        self.assertEqual(advice.attribution["plan"], PLAN_TEXT)
        self.assertEqual(advice.attribution["parent_id"], "p")
        self.assertEqual(ledger.spent("coordination"), 380)
        self.assertEqual(ledger.spent("mutation"), 0)
        # The planning prompt carried the parent's code and fitness
        prompt_text = client.calls[0]["messages"][-1]["content"]
        self.assertIn("def f():", prompt_text)
        self.assertIn("0.5000", prompt_text)
        self.assertIn("first plan for this lineage", prompt_text)

    def test_no_parent_or_no_llm_is_noop(self):
        module, ledger, client = self.make_module()
        advice = asyncio.run(module.advise(make_ctx(parent=None)))
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(len(client.calls), 0)

        bare = PESPlannerModule()  # llm=None
        advice = asyncio.run(bare.advise(make_ctx()))
        self.assertEqual(advice.prompt_block, "")

    def test_llm_failure_degrades_to_noop_advice(self):
        module, ledger, client = self.make_module(fail_with=RuntimeError("boom"))
        advice = asyncio.run(module.advise(make_ctx()))
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.attribution, {})

    def test_budget_exhaustion_propagates(self):
        # First call crosses the 1-token cap and is still served (ledger
        # contract); the next pre-flight ensure() must raise through advise()
        module, _, _ = self.make_module(budget=1)
        asyncio.run(module.advise(make_ctx()))
        with self.assertRaises(BudgetExhausted):
            asyncio.run(module.advise(make_ctx()))

    # ---------------------------------------------------- lineage memory

    def test_prior_plan_and_outcome_reach_next_planning_prompt(self):
        module, _, client = self.make_module()
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        child = make_view(pid="child-1", fitness=0.7)
        module.report_result(ctx, child, advice.attribution, eval_failed=False)

        # The child becomes the next parent: its plan + outcome must be shown
        ctx2 = make_ctx(parent=child)
        asyncio.run(module.advise(ctx2))
        prompt_text = client.calls[1]["messages"][-1]["content"]
        self.assertIn(PLAN_TEXT, prompt_text)
        self.assertIn(IMPROVED, prompt_text)
        self.assertIn("0.5000 -> 0.7000", prompt_text)

    # ------------------------------------------------ outcome classification

    def outcome_for(self, child_fitness=None, eval_failed=False, child_missing=False):
        module, _, _ = self.make_module()
        ctx = make_ctx()  # parent fitness 0.5
        advice = asyncio.run(module.advise(ctx))
        child = None if child_missing else make_view(pid="c", fitness=child_fitness)
        module.report_result(ctx, child, advice.attribution, eval_failed=eval_failed)
        return module._plans.get("c", {}).get("outcome")

    def test_child_above_parent_is_improved(self):
        self.assertEqual(self.outcome_for(child_fitness=0.9), IMPROVED)

    def test_child_below_parent_is_regressed(self):
        self.assertEqual(self.outcome_for(child_fitness=0.1), REGRESSED)

    def test_child_equal_parent_is_stale(self):
        self.assertEqual(self.outcome_for(child_fitness=0.5), STALE)

    def test_eval_failure_is_failed(self):
        self.assertEqual(self.outcome_for(child_fitness=0.9, eval_failed=True), FAILED)

    def test_missing_child_stores_nothing(self):
        self.assertIsNone(self.outcome_for(child_missing=True))

    # ------------------------------------------------------------- state

    def test_state_dict_round_trip_and_json_serializable(self):
        module, _, _ = self.make_module()
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        module.report_result(ctx, make_view(pid="c", fitness=0.9), advice.attribution, False)

        state = json.loads(json.dumps(module.state_dict()))
        module2, _, _ = self.make_module()
        module2.load_state_dict(state)
        self.assertEqual(module2._plans, module._plans)
        json.dumps(module2.log_snapshot())
        self.assertEqual(module2.log_snapshot()["outcomes"], {IMPROVED: 1})

    def test_long_parent_code_is_truncated_in_prompt(self):
        module, _, client = self.make_module(max_code_chars=50)
        long_code = "x = 1\n" * 100
        asyncio.run(module.advise(make_ctx(parent=make_view(code=long_code))))
        prompt_text = client.calls[0]["messages"][-1]["content"]
        self.assertIn("# ... (truncated)", prompt_text)

    def test_registered_in_module_registry(self):
        module = build_coordination_module("pes", params={"max_code_chars": 123})
        self.assertIsInstance(module, PESPlannerModule)
        self.assertEqual(module.max_code_chars, 123)


if __name__ == "__main__":
    unittest.main()
