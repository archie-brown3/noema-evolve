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


REFLECTION_TEXT = "The ring loop overran the array bound; cap the index at n-1 next time."


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


def _is_reflection_call(params) -> bool:
    return any(
        "# Outcome to Explain" in m.get("content", "") for m in params.get("messages", [])
    )


def make_dual_client(plan_text=PLAN_TEXT, reflection_text=REFLECTION_TEXT, fail_reflection=None):
    """Fake client returning a distinct response for reflection vs planning calls."""
    calls = []

    async def create(**params):
        calls.append(params)
        if _is_reflection_call(params):
            if fail_reflection is not None:
                raise fail_reflection
            content = reflection_text
        else:
            content = plan_text
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=80),
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls
    )
    return client


class TestPESPlannerModule(unittest.TestCase):
    def make_module(
        self, response=PLAN_TEXT, fail_with=None, budget=100_000, client=None, **params
    ):
        ledger = TokenLedger(total_budget_tokens=budget)
        client = client or make_plan_client(response, fail_with=fail_with)
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

    def test_domain_context_reaches_planner_system_message_not_user_prompt(self):
        module, ledger, client = self.make_module(
            domain_context="Use an explicit constructor, not iterative search."
        )
        asyncio.run(module.advise(make_ctx()))

        system_text = client.calls[0]["messages"][0]["content"]
        user_text = client.calls[0]["messages"][-1]["content"]
        self.assertIn("Use an explicit constructor, not iterative search.", system_text)
        self.assertIn("# Problem Domain", system_text)
        self.assertNotIn("Use an explicit constructor", user_text)

    def test_no_domain_context_leaves_planner_system_message_unchanged(self):
        module, ledger, client = self.make_module()
        asyncio.run(module.advise(make_ctx()))
        system_text = client.calls[0]["messages"][0]["content"]
        self.assertNotIn("# Problem Domain", system_text)

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

    def test_planning_failure_still_records_lineage_for_a_real_child(self):
        # Task 0042. A failed PLANNING call degrades advise() to a no-op
        # Advice() (empty attribution), but the MUTATION may still succeed and
        # produce a real, evaluated child. That child must stay visible to PES's
        # own lineage memory: dropping it made every future descendant report
        # "None — first plan for this lineage" despite real history existing,
        # and the failures correlate with cluster transients — i.e. they
        # silently degrade exactly the mechanism this arm is measuring.
        module, _, client = self.make_module(fail_with=RuntimeError("boom"))
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        self.assertEqual(advice.attribution, {})  # planning really did fail

        child = make_view(pid="child-1", fitness=0.7)
        module.report_result(ctx, child, advice.attribution, eval_failed=False)

        # The child is in lineage memory, with its outcome, despite no plan
        self.assertIn("child-1", module._plans)
        entry = module._plans["child-1"]
        self.assertEqual(entry["plan"], "")
        self.assertEqual(entry["parent_id"], ctx.parent.id)
        self.assertEqual(entry["child_fitness"], 0.7)
        # ...but nothing was enqueued for reflection: there is no plan to reflect on
        self.assertEqual(len(module._pending_reflections), 0)

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

    # ------------------------------------------------ reflection (Phase 2)

    def test_report_result_makes_no_llm_call(self):
        # The reflection call happens at on_generation_end, never in
        # report_result — report_result stays sync/no-LLM by contract.
        module, _, client = self.make_module(client=make_dual_client())
        ctx = make_ctx()
        asyncio.run(module.advise(ctx))  # 1 planning call
        module.report_result(ctx, make_view(pid="c", fitness=0.9), {"plan": PLAN_TEXT}, False)
        self.assertEqual(len(client.calls), 1)  # no new call from report_result
        self.assertEqual(len(module._pending_reflections), 1)  # but it enqueued

    def test_reflection_runs_at_generation_end_on_coordination_account(self):
        module, ledger, client = self.make_module(client=make_dual_client())
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        module.report_result(ctx, make_view(pid="c", fitness=0.9), advice.attribution, False)
        spent_before = ledger.spent("coordination")
        asyncio.run(module.on_generation_end(make_ctx(parent=None)))
        # A second call happened, it was the reflection, charged to coordination
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(_is_reflection_call(client.calls[1]))
        self.assertGreater(ledger.spent("coordination"), spent_before)
        self.assertEqual(len(module._pending_reflections), 0)  # queue drained

    def test_reflection_reaches_next_planning_prompt(self):
        module, _, client = self.make_module(client=make_dual_client())
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        child = make_view(pid="c", fitness=0.7)
        module.report_result(ctx, child, advice.attribution, False)
        asyncio.run(module.on_generation_end(make_ctx(parent=None)))

        asyncio.run(module.advise(make_ctx(parent=child)))
        prompt_text = client.calls[-1]["messages"][-1]["content"]
        self.assertIn("## Reflection on that outcome", prompt_text)
        self.assertIn(REFLECTION_TEXT, prompt_text)

    def test_reflection_budget_exhaustion_propagates(self):
        module, _, _ = self.make_module(budget=1, client=make_dual_client())
        ctx = make_ctx()
        asyncio.run(module.advise(ctx))  # first call served, crosses the 1-token cap
        module.report_result(ctx, make_view(pid="c", fitness=0.9), {"plan": PLAN_TEXT}, False)
        with self.assertRaises(BudgetExhausted):
            asyncio.run(module.on_generation_end(make_ctx(parent=None)))

    def test_reflection_llm_failure_degrades_but_keeps_outcome(self):
        client = make_dual_client(fail_reflection=RuntimeError("boom"))
        module, _, _ = self.make_module(client=client)
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        module.report_result(ctx, make_view(pid="c", fitness=0.9), advice.attribution, False)
        asyncio.run(module.on_generation_end(make_ctx(parent=None)))
        self.assertEqual(module._plans["c"]["reflection"], "")
        self.assertEqual(module._plans["c"]["outcome"], IMPROVED)  # plan/outcome intact

    def test_reflection_disabled_makes_no_call_and_drops_queue(self):
        module, _, client = self.make_module(client=make_dual_client(), reflection_enabled=False)
        ctx = make_ctx()
        asyncio.run(module.advise(ctx))
        module.report_result(ctx, make_view(pid="c", fitness=0.9), {"plan": PLAN_TEXT}, False)
        self.assertEqual(len(module._pending_reflections), 0)  # never enqueued
        asyncio.run(module.on_generation_end(make_ctx(parent=None)))
        self.assertEqual(len(client.calls), 1)  # planning call only

    def test_stderr_reaches_reflection_prompt_on_failed(self):
        module, _, client = self.make_module(client=make_dual_client())
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        failed_child = ProgramView(
            id="c", code="def f(): pass", fitness=0.0,
            metrics={"score": 0.0}, metadata={"stderr": "IndexError: boom-42"},
        )
        module.report_result(ctx, failed_child, advice.attribution, eval_failed=True)
        asyncio.run(module.on_generation_end(make_ctx(parent=None)))
        reflection_prompt = client.calls[1]["messages"][-1]["content"]
        self.assertIn("IndexError: boom-42", reflection_prompt)

    def test_pending_reflections_survive_state_round_trip(self):
        module, _, _ = self.make_module(client=make_dual_client())
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        module.report_result(ctx, make_view(pid="c", fitness=0.9), advice.attribution, False)
        self.assertEqual(len(module._pending_reflections), 1)

        state = json.loads(json.dumps(module.state_dict()))  # must be JSON-serializable
        module2, _, _ = self.make_module(client=make_dual_client())
        module2.load_state_dict(state)
        self.assertEqual(module2._pending_reflections, module._pending_reflections)

    # -------------------------------------------- cross-lineage diversity

    def test_recent_strategies_reach_a_fresh_lineage_prompt(self):
        module, _, client = self.make_module()
        # Seed plans from OTHER lineages (a fresh parent shares no ancestry).
        module._plans["other-1"] = {
            "plan": "# Plan\n## Strategy\n- Tile the plane with hexagons\n## Action Steps\n1. go",
            "outcome": FAILED,
            "parent_fitness": 0.3,
            "child_fitness": 0.0,
        }
        module._plans["other-2"] = {
            "plan": "# Plan\n## Strategy\n- Stack circles in rows of decreasing size\n## Action\n1. go",
            "outcome": IMPROVED,
            "parent_fitness": 0.3,
            "child_fitness": 0.5,
        }
        asyncio.run(module.advise(make_ctx(parent=make_view(pid="fresh"))))
        prompt_text = client.calls[0]["messages"][-1]["content"]
        self.assertIn("# Recently Attempted Elsewhere", prompt_text)
        self.assertIn("Tile the plane with hexagons", prompt_text)
        self.assertIn("Stack circles in rows of decreasing size", prompt_text)
        self.assertIn(f"[{FAILED}]", prompt_text)

    def test_recent_strategies_excludes_the_lineages_own_entry(self):
        # The parent's own last plan lives in prior_block, not "Recently Attempted".
        module, _, client = self.make_module()
        module._plans["p"] = {
            "plan": "# Plan\n## Strategy\n- Only this lineages idea\n## Action\n1. go",
            "outcome": STALE,
            "parent_fitness": 0.5,
            "child_fitness": 0.5,
        }
        asyncio.run(module.advise(make_ctx(parent=make_view(pid="p"))))
        prompt_text = client.calls[0]["messages"][-1]["content"]
        self.assertNotIn("# Recently Attempted Elsewhere", prompt_text)

    def test_no_recent_block_when_no_plans_yet(self):
        module, _, client = self.make_module()
        asyncio.run(module.advise(make_ctx()))
        self.assertNotIn("# Recently Attempted Elsewhere", client.calls[0]["messages"][-1]["content"])

    # -------------------------------------------- retry_advice (Stage 2)

    def test_retry_advice_returns_reflection_when_present(self):
        module, _, _ = self.make_module()
        ctx = make_ctx(parent=make_view(pid="p"))
        module._plans["p"] = {
            "plan": PLAN_TEXT,
            "outcome": FAILED,
            "parent_fitness": 0.5,
            "child_fitness": 0.0,
            "reflection": REFLECTION_TEXT,
        }
        advice = asyncio.run(module.retry_advice(ctx, "IndexError", 0))
        self.assertIn("# Reflection on the lineage's last failure", advice)
        self.assertIn(REFLECTION_TEXT, advice)
        self.assertIn("Use this causal explanation", advice)

    def test_retry_advice_returns_empty_when_no_parent(self):
        module, _, _ = self.make_module()
        ctx = make_ctx(parent=None)
        module._plans["p"] = {"reflection": REFLECTION_TEXT}
        advice = asyncio.run(module.retry_advice(ctx, "err", 0))
        self.assertEqual(advice, "")

    def test_retry_advice_returns_empty_when_no_reflection_yet(self):
        # Fresh lineage: parent exists but no plan/reflection stored
        module, _, _ = self.make_module()
        ctx = make_ctx(parent=make_view(pid="fresh"))
        advice = asyncio.run(module.retry_advice(ctx, "err", 0))
        self.assertEqual(advice, "")

    def test_retry_advice_returns_empty_when_reflection_is_blank(self):
        module, _, _ = self.make_module()
        ctx = make_ctx(parent=make_view(pid="p"))
        module._plans["p"] = {"reflection": ""}  # failed reflection call -> ""
        advice = asyncio.run(module.retry_advice(ctx, "err", 0))
        self.assertEqual(advice, "")

    def test_null_coordination_retry_advice_is_noop(self):
        # Regression guard: Null must NOT carry reflection (confound control)
        from noema.coordination.base import NullCoordination
        ctx = make_ctx()
        advice = asyncio.run(NullCoordination().retry_advice(ctx, "err", 0))
        self.assertEqual(advice, "")

    # --------------------------------------- executor_mode="directive" (0065)

    def test_advisory_is_default_and_byte_identical(self):
        # Regression pin: default executor_mode leaves advisory behavior untouched
        module, _, _ = self.make_module()
        self.assertEqual(module.executor_mode, "advisory")
        advice = asyncio.run(module.advise(make_ctx()))
        self.assertEqual(advice.prompt_block, PLAN_TEXT)
        self.assertEqual(advice.system_block, "")
        self.assertNotIn("full_executor_prompt", advice.attribution)

    def test_directive_mode_returns_full_executor_prompt(self):
        from noema.coordination.pes.executor import (
            EXECUTOR_SYSTEM_WITH_PLAN,
            EXECUTOR_USER_WITH_PLAN,
        )

        module, _, _ = self.make_module(executor_mode="directive")
        advice = asyncio.run(module.advise(make_ctx()))

        self.assertEqual(advice.system_block, EXECUTOR_SYSTEM_WITH_PLAN)
        self.assertIn("# Task Information", advice.prompt_block)
        self.assertIn("# Plan", advice.prompt_block)
        self.assertIn("# Parent Solution", advice.prompt_block)
        self.assertIn("# Requirement", advice.prompt_block)
        self.assertIn(PLAN_TEXT, advice.prompt_block)
        self.assertIs(advice.attribution["full_executor_prompt"], True)
        self.assertNotIn("{previous_attempts}", advice.prompt_block)
        self.assertNotEqual(EXECUTOR_USER_WITH_PLAN, advice.prompt_block)  # was formatted

    def test_directive_mode_includes_parent_solution_json(self):
        module, _, _ = self.make_module(executor_mode="directive")
        parent = make_view(pid="p", fitness=0.75, code="def g():\n    return 2\n")
        advice = asyncio.run(module.advise(make_ctx(parent=parent)))

        parsed = json.loads(
            advice.prompt_block.split("# Parent Solution\n", 1)[1].split("\n\n## Filed", 1)[0]
        )
        self.assertEqual(parsed["solution"], "def g():\n    return 2\n")
        self.assertEqual(parsed["score"], 0.75)

    def test_directive_mode_no_plan_is_noop(self):
        module, _, client = self.make_module(executor_mode="directive", fail_with=RuntimeError("boom"))
        advice = asyncio.run(module.advise(make_ctx()))
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.attribution, {})

    def test_directive_retry_advice_is_empty(self):
        # LoongFlow retries carry evaluation text via build_retry_prompt, not
        # the reflection-suffix path — directive retry_advice always yields "".
        module, _, _ = self.make_module(executor_mode="directive")
        ctx = make_ctx(parent=make_view(pid="p"))
        module._plans["p"] = {"reflection": REFLECTION_TEXT}
        advice = asyncio.run(module.retry_advice(ctx, "err", 1))
        self.assertEqual(advice, "")

    def test_build_retry_prompt_formats_previous_attempts(self):
        module, _, _ = self.make_module(executor_mode="directive")
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))

        retry1 = module.build_retry_prompt(ctx, advice.attribution, 1, "IndexError: boom")
        self.assertIn(
            "Round 1, Candidate 0, Evaluation Result: IndexError: boom\n\n",
            retry1["user"],
        )
        self.assertIn(PLAN_TEXT, retry1["user"])
        self.assertEqual(retry1["system"], advice.system_block)

        # Accumulates across successive retries of the same mutation
        retry2 = module.build_retry_prompt(ctx, advice.attribution, 2, "still failing")
        self.assertIn("Round 1, Candidate 0, Evaluation Result: IndexError: boom\n\n", retry2["user"])
        self.assertIn("Round 2, Candidate 0, Evaluation Result: still failing\n\n", retry2["user"])

    def test_build_retry_prompt_returns_none_for_advisory(self):
        module, _, _ = self.make_module()
        ctx = make_ctx()
        advice = asyncio.run(module.advise(ctx))
        self.assertIsNone(module.build_retry_prompt(ctx, advice.attribution, 1, "err"))

    def test_directive_attempts_do_not_bleed_into_the_next_mutation(self):
        # One Executor instance serves every iteration and island, and the
        # attempt log lives on it. A later mutation must never see an earlier
        # lineage's failures: build_advice clears the log. (Iterations run
        # sequentially in the controller; this pins the property so a future
        # refactor cannot silently start leaking one lineage's errors into
        # another's prompt — which would corrupt the treatment.)
        module, _, _ = self.make_module(executor_mode="directive")

        first_ctx = make_ctx(parent=make_view(pid="lineage-a"))
        first_advice = asyncio.run(module.advise(first_ctx))
        retry = module.build_retry_prompt(
            first_ctx, first_advice.attribution, 1, "SECRET-A: divide by zero"
        )
        self.assertIn("SECRET-A", retry["user"])

        second_ctx = make_ctx(parent=make_view(pid="lineage-b"))
        second_advice = asyncio.run(module.advise(second_ctx))
        # Fresh mutation: empty attempt log, no trace of lineage A.
        self.assertNotIn("SECRET-A", second_advice.prompt_block)
        self.assertNotIn("Round 1, Candidate 0", second_advice.prompt_block)
        # ...and its own first retry starts the round count over.
        second_retry = module.build_retry_prompt(
            second_ctx, second_advice.attribution, 1, "TypeError: b"
        )
        self.assertNotIn("SECRET-A", second_retry["user"])
        self.assertIn("Round 1, Candidate 0, Evaluation Result: TypeError: b", second_retry["user"])


if __name__ == "__main__":
    unittest.main()
