"""
Tests for noema.substrate.prompts — the identical-prompts-across-arms guarantee
"""

import asyncio
import random
import unittest
import uuid
from types import SimpleNamespace

from openevolve.config import PromptConfig
from openevolve.database import Program

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.coordination.base import GenerationContext
from noema.coordination.pes.module import PESPlannerModule
from noema.substrate.prompts import (
    COORDINATION_HEADER,
    build_mutation_prompt,
    inject_advice,
    make_prompt_sampler,
)
from noema.substrate.views import ProgramView
from noema.controller import NoemaController


def make_parent() -> Program:
    return Program(
        id=str(uuid.uuid4()),
        code="def f():\n    return 1\n",
        language="python",
        metrics={"combined_score": 0.5},
    )


def build(sampler, parent):
    return build_mutation_prompt(
        sampler,
        parent=parent,
        top_programs=[],
        previous_programs=[],
        inspirations=[],
        language="python",
        iteration=3,
        diff_based_evolution=True,
        feature_dimensions=["complexity", "diversity"],
    )


class TestPromptAssembly(unittest.TestCase):
    def test_stochasticity_rejected(self):
        with self.assertRaises(ValueError):
            make_prompt_sampler(PromptConfig(use_template_stochasticity=True))

    def test_prompt_deterministic_across_builds(self):
        parent = make_parent()
        sampler_a = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        sampler_b = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt_a = build(sampler_a, parent)
        prompt_b = build(sampler_b, parent)
        self.assertEqual(prompt_a["system"], prompt_b["system"])
        self.assertEqual(prompt_a["user"], prompt_b["user"])

    def test_empty_advice_is_byte_identical(self):
        # The coordination-OFF arm: injecting empty blocks must not change a byte
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt = build(sampler, make_parent())
        injected = inject_advice(prompt, prompt_block="", system_block="")
        self.assertEqual(injected["system"], prompt["system"])
        self.assertEqual(injected["user"], prompt["user"])

    def test_advice_appends_suffix_only(self):
        # The coordination-ON arm: shared prefix stays byte-identical, block is a suffix
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt = build(sampler, make_parent())
        injected = inject_advice(
            prompt, prompt_block="- Use vectorized operations", system_block="Focus on speed."
        )
        self.assertTrue(injected["user"].startswith(prompt["user"]))
        self.assertTrue(injected["system"].startswith(prompt["system"]))
        self.assertEqual(
            injected["user"],
            prompt["user"] + COORDINATION_HEADER + "- Use vectorized operations",
        )
        self.assertIn("Focus on speed.", injected["system"])


class TestOperatorTemplatePassthrough(unittest.TestCase):
    """template_key/parent2 passthrough (task 0027) — must not disturb the
    existing legacy call path (test_prompt_deterministic_across_builds etc.
    above use no template_key/parent2 and must stay green unchanged)."""

    def test_make_prompt_sampler_registers_operator_templates(self):
        from noema.substrate.operators import OPERATOR_TEMPLATES

        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        for template_key in OPERATOR_TEMPLATES:
            self.assertIn(template_key, sampler.template_manager.templates)

    def test_arity_two_template_includes_parent2_code(self):
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        parent = make_parent()
        parent2 = Program(
            id="p2", code="def g():\n    return 2\n", language="python", metrics={}
        )
        prompt = build_mutation_prompt(
            sampler,
            parent=parent,
            top_programs=[],
            previous_programs=[],
            inspirations=[],
            language="python",
            iteration=0,
            diff_based_evolution=True,
            feature_dimensions=[],
            template_key="eoh_e2_user",
            parent2=parent2,
        )
        self.assertIn("def g():\n    return 2", prompt["user"])
        self.assertNotIn("{parent2_program}", prompt["user"])

    def test_arity_one_template_no_parent2_does_not_error_or_leak_placeholder(self):
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt = build_mutation_prompt(
            sampler,
            parent=make_parent(),
            top_programs=[],
            previous_programs=[],
            inspirations=[],
            language="python",
            iteration=0,
            diff_based_evolution=True,
            feature_dimensions=[],
            template_key="eoh_m1_user",
            parent2=None,
        )
        self.assertNotIn("{parent2_program}", prompt["user"])
        self.assertNotIn("{metrics}", prompt["user"])
        self.assertNotIn("{current_program}", prompt["user"])


class TestRetryPromptSuffix(unittest.TestCase):
    def test_retry_suffix_structure(self):
        suffix = NoemaController._build_retry_suffix(
            None, error_text="IndexError: list index out of range", attempt=1
        )
        self.assertIn("# Retry After Failure", suffix)
        self.assertIn("Your previous attempt failed", suffix)
        self.assertIn("IndexError: list index out of range", suffix)
        self.assertIn("Produce a corrected program", suffix)
        self.assertIn("Re-output the full code", suffix)

    def test_retry_suffix_includes_error_text(self):
        suffix = NoemaController._build_retry_suffix(
            None, error_text="no parseable code block found in the response", attempt=2
        )
        self.assertIn("no parseable code block found in the response", suffix)

    def test_retry_suffix_is_arm_agnostic(self):
        # Same method, same output regardless of coordination module
        suffix = NoemaController._build_retry_suffix(
            None, error_text="generated code length 15000 exceeds max 10000", attempt=0
        )
        self.assertIn("generated code length 15000 exceeds max 10000", suffix)
        self.assertNotIn("reflection", suffix.lower())
        self.assertNotIn("plan", suffix.lower())

    def test_reflection_suffix_structure(self):
        # The PES reflection block appended after the raw-error retry suffix.
        # Locked substrings per spec (prompt-identity guard for Stage 2).
        reflection_text = "The loop overran the array bound; cap the index at n-1."
        reflection_suffix = (
            "\n# Reflection on the lineage's last failure\n"
            f"{reflection_text}\n"
            "Use this causal explanation to guide the corrected mutation."
        )
        self.assertIn("# Reflection on the lineage's last failure", reflection_suffix)
        self.assertIn("Use this causal explanation", reflection_suffix)
        self.assertIn(reflection_text, reflection_suffix)


class TestFaithfulPlannerConstants(unittest.TestCase):
    """pes-faithful planner prompt constants (task 0063, C1).

    Pins the load-bearing recast structure per the design note
    'PES Faithful Prompt Recast Design — 2026-07-10' §1: the exact headings the
    host slices on, the enforcement clauses, the kept-verbatim pressure lines
    (they are the treatment), and the absence of tool/workspace residue and of
    the custom-only recent_block (Decision #27)."""

    def test_headings_mandates_and_pressure_lines_present(self):
        from noema.coordination.pes.planner import (
            FAITHFUL_PLANNER_MIN_TOKENS,
            FAITHFUL_PLANNER_SYSTEM,
            FAITHFUL_PLANNER_USER_TEMPLATE,
            FINAL_PLAN_HEADING,
        )

        # Extraction anchor: exact heading, stated in step 9, the enforcement
        # closer, and the <Example> (three occurrences minimum).
        self.assertEqual(
            FINAL_PLAN_HEADING, "### Final Child Solution Generation Plan"
        )
        self.assertGreaterEqual(
            FAITHFUL_PLANNER_USER_TEMPLATE.count(FINAL_PLAN_HEADING), 3
        )
        # Outline headings mandated twice (step 4 + IMPORTANT enforcement).
        for heading in ("## Plan Outline 1", "## Plan Outline 2", "## Plan Outline 3"):
            self.assertGreaterEqual(
                FAITHFUL_PLANNER_USER_TEMPLATE.count(f"`{heading}`"), 2
            )
        # Self-containment rule (prevents dangling outline references after
        # the host slices on the last heading).
        self.assertIn(
            "handed to the Phase 2 executor verbatim", FAITHFUL_PLANNER_USER_TEMPLATE
        )
        self.assertIn("must be self-contained", FAITHFUL_PLANNER_USER_TEMPLATE)
        # Pressure lines kept verbatim — they are part of the treatment.
        self.assertIn("PUNISHED and DISMISSED", FAITHFUL_PLANNER_SYSTEM)
        self.assertIn("This is your last chance", FAITHFUL_PLANNER_USER_TEMPLATE)
        # Global-perspective strategies and mandates kept verbatim.
        self.assertIn("1 + 1 > 2", FAITHFUL_PLANNER_SYSTEM)
        self.assertIn("Multi-Start Mandate", FAITHFUL_PLANNER_USER_TEMPLATE)
        self.assertIn("CRITICAL THOUGHT PROCESS", FAITHFUL_PLANNER_USER_TEMPLATE)
        # Template variables the host fills (island block is the conditional
        # slot from task 0061).
        for var in (
            "{task_info}",
            "{parent_solution}",
            "{island_num}",
            "{parent_island}",
            "{island_status_block}",
        ):
            self.assertIn(var, FAITHFUL_PLANNER_USER_TEMPLATE)
        # Sizing floor for the single-call recast (design note §1.4).
        self.assertGreaterEqual(FAITHFUL_PLANNER_MIN_TOKENS, 2048)

    def test_no_tool_workspace_residue_and_no_recent_block(self):
        from noema.coordination.pes.planner import (
            FAITHFUL_PLANNER_SYSTEM,
            FAITHFUL_PLANNER_USER_TEMPLATE,
        )

        both = FAITHFUL_PLANNER_SYSTEM + FAITHFUL_PLANNER_USER_TEMPLATE
        # No interactive-tool or workspace residue may survive the recast.
        for residue in (
            "Get_Memory_Status",
            "generate_final_answer",
            "Write Tool",
            "Write tool",
            "plan_1.txt",
            "# Workspace",
            "{workspace}",
        ):
            self.assertNotIn(residue, both)
        # The custom-only recent_block is deliberately absent (Decision #27).
        self.assertNotIn("{recent_block}", both)
        self.assertNotIn("Recently Attempted Elsewhere", both)


# ---------------------------------------------------------------- PES fixtures

FAITHFUL_COMPLETION = (
    "## Plan Outline 1\nA\n## Plan Outline 2\nB\n## Plan Outline 3\nC\n"
    "Comparison: outline B is the most robust.\n"
    "### Final Child Solution Generation Plan\n\n"
    "**Best Plan:** apply `scipy.optimize.minimize` with method 'SLSQP'."
)


def make_pes_ctx() -> GenerationContext:
    parent = ProgramView(
        id="parent-1",
        code="def f():\n    return 1\n",
        fitness=0.5,
        metrics={"score": 0.5},
    )
    return GenerationContext(
        iteration=0,
        generation=0,
        island=0,
        parent=parent,
        best_fitness_history=[0.1, 0.2],
        avg_fitness_history=[0.05, 0.1],
    )


def make_pes_module(response_text=FAITHFUL_COMPLETION, llm_max_tokens=None, **params):
    """PESPlannerModule over a fake chat client; returns (module, captured calls)."""
    calls = []

    async def create(**call_params):
        calls.append(call_params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=80),
        )

    llm = BudgetedLLM(
        model="fake-model",
        ledger=TokenLedger(total_budget_tokens=100_000),
        account="coordination",
        tag="pes.coordination",
        max_tokens=llm_max_tokens,
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        retries=0,
        retry_delay=0.0,
    )
    return PESPlannerModule(config=params, llm=llm, rng=random.Random(0)), calls


class TestFaithfulPlannerPath(unittest.TestCase):
    """prompt_variant wiring (task 0063 C2) — prompt identity at advise() time."""

    def test_custom_default_prompt_byte_identical_regression(self):
        # Default variant is "custom": the emitted planning prompt must be the
        # pre-0063 bytes exactly (template fill pinned here), with the plain
        # system prompt and no max_tokens override on the call.
        from noema.coordination.pes.planner import PLANNER_SYSTEM, PLANNER_USER_TEMPLATE

        module, calls = make_pes_module()
        ctx = make_pes_ctx()
        asyncio.run(module.advise(ctx))
        expected_user = PLANNER_USER_TEMPLATE.format(
            fitness=0.5,
            metrics={"score": 0.5},
            code=ctx.parent.code,
            prior_block="None — first plan for this lineage.",
            recent_block="",
            best_history=[0.1, 0.2],
            avg_history=[0.05, 0.1],
        )
        self.assertEqual(module.prompt_variant, "custom")
        self.assertEqual(
            calls[0]["messages"],
            [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": expected_user},
            ],
        )
        self.assertNotIn("max_tokens", calls[0])

    def test_faithful_prompt_at_advise_time_with_provider(self):
        # Deferred 0061 verifier condition: the island status block appears in
        # the FAITHFUL planning prompt at advise() time with the provider's
        # values; and the advice carries only the extracted final-plan slice.
        from noema.coordination.pes.planner import FAITHFUL_PLANNER_SYSTEM

        module, calls = make_pes_module(
            prompt_variant="faithful",
            island_bests_provider=lambda: [0.5, 0.9812],
        )
        advice = asyncio.run(module.advise(make_pes_ctx()))
        system, user = calls[0]["messages"][0], calls[0]["messages"][1]
        self.assertEqual(system["content"], FAITHFUL_PLANNER_SYSTEM)
        self.assertIn(
            "Island status (best score per island): island_0: 0.5000, island_1: 0.9812",
            user["content"],
        )
        self.assertIn("The current database includes 2 islands", user["content"])
        self.assertNotIn("Recently Attempted Elsewhere", user["content"])
        # Executor sees the final plan slice only — never the three outlines.
        self.assertIn("apply `scipy.optimize.minimize`", advice.prompt_block)
        self.assertNotIn("## Plan Outline", advice.prompt_block)

    def test_faithful_prompt_block_absent_without_provider(self):
        # Deferred 0061 verifier condition, second half: no provider -> the
        # block renders "" (absent), strategies stay verbatim but inert.
        module, calls = make_pes_module(prompt_variant="faithful")
        asyncio.run(module.advise(make_pes_ctx()))
        user = calls[0]["messages"][1]["content"]
        self.assertNotIn("Island status", user)
        self.assertIn("# Database", user)

    def test_faithful_prompt_deterministic(self):
        # Same module state + same ctx -> byte-identical prompt on every call
        # (guarantee triad #3; advise() does not mutate _plans).
        module, calls = make_pes_module(
            prompt_variant="faithful", island_bests_provider=lambda: [0.5, 0.9812]
        )
        asyncio.run(module.advise(make_pes_ctx()))
        asyncio.run(module.advise(make_pes_ctx()))
        self.assertEqual(calls[0]["messages"], calls[1]["messages"])

    def test_faithful_max_tokens_floor(self):
        # A configured cap below the floor is raised to it; above is kept;
        # unset stays unset (no cap to raise).
        for configured, expected in ((1024, 2048), (4096, 4096)):
            module, calls = make_pes_module(
                llm_max_tokens=configured, prompt_variant="faithful"
            )
            asyncio.run(module.advise(make_pes_ctx()))
            self.assertEqual(calls[0].get("max_tokens"), expected)
        module, calls = make_pes_module(prompt_variant="faithful")
        asyncio.run(module.advise(make_pes_ctx()))
        self.assertNotIn("max_tokens", calls[0])

    def test_raising_provider_propagates_out_of_advise(self):
        # Fail-loud posture (0061 verifier finding 9, decided in 0063): a
        # broken provider is a host bug; silently dropping the block would
        # silently change the treatment mid-run.
        def broken_provider():
            raise RuntimeError("db exploded")

        module, _ = make_pes_module(
            prompt_variant="faithful", island_bests_provider=broken_provider
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(module.advise(make_pes_ctx()))

    def test_missing_heading_falls_back_with_logged_warning(self):
        module, _ = make_pes_module(
            response_text="## Plan Outline 1\nA\nno final heading here",
            prompt_variant="faithful",
        )
        with self.assertLogs("noema.coordination.pes.planner", level="WARNING") as logs:
            advice = asyncio.run(module.advise(make_pes_ctx()))
        self.assertTrue(any("final-plan heading missing" in m for m in logs.output))
        # Fallback: the full completion becomes the plan.
        self.assertIn("no final heading here", advice.prompt_block)

    def test_invalid_prompt_variant_rejected(self):
        with self.assertRaises(ValueError):
            make_pes_module(prompt_variant="verbatim")


class TestExtractFinalPlan(unittest.TestCase):
    def test_normal_extraction(self):
        from noema.coordination.pes.planner import extract_final_plan

        plan, extracted = extract_final_plan(FAITHFUL_COMPLETION)
        self.assertTrue(extracted)
        self.assertEqual(
            plan, "**Best Plan:** apply `scipy.optimize.minimize` with method 'SLSQP'."
        )

    def test_multiple_headings_takes_last(self):
        from noema.coordination.pes.planner import (
            FINAL_PLAN_HEADING,
            extract_final_plan,
        )

        completion = (
            f"{FINAL_PLAN_HEADING}\nEchoed from the example.\n"
            f"outlines...\n{FINAL_PLAN_HEADING}\nThe real plan."
        )
        plan, extracted = extract_final_plan(completion)
        self.assertTrue(extracted)
        self.assertEqual(plan, "The real plan.")

    def test_missing_heading_returns_full_text_unextracted(self):
        from noema.coordination.pes.planner import extract_final_plan

        plan, extracted = extract_final_plan("  just prose, no heading  ")
        self.assertFalse(extracted)
        self.assertEqual(plan, "just prose, no heading")


if __name__ == "__main__":
    unittest.main()
