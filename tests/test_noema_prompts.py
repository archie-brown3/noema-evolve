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


def make_pes_ctx(parent=None) -> GenerationContext:
    parent = parent or ProgramView(
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


# Frozen bytes of the custom (pes-custom) reflection constants — an edit to
# the constants themselves must not slip past the regression pin below.
CUSTOM_REFLECTION_SYSTEM_SHA = (
    "c02f780880a5f50996086913b37de6c6bf26569569f042c192e0a798f28f84cf"
)
CUSTOM_REFLECTION_USER_SHA = (
    "b0496e5af86ac50f1d3207a7e5d8f00b699a0e089a7c21b1b9ef9438fe8abadb"
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
        # Frozen-hash pin of the custom template bytes themselves, so an edit
        # to the constants can't slip past the fixture-side render above
        # (0063 verifier finding 2).
        import hashlib

        self.assertEqual(
            hashlib.sha256(PLANNER_SYSTEM.encode()).hexdigest(),
            "b7f5d5917ec3da6cf9b0f204a727fa29ab4137a1b79fb59c8f0a4c9082bf434b",
        )
        self.assertEqual(
            hashlib.sha256(PLANNER_USER_TEMPLATE.encode()).hexdigest(),
            "e54eaca3fd4401209063fb82fe8fb235c0f951072b890c936dec982161ab48a9",
        )

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
        # unset sends the floor explicitly (local servers may default an
        # omitted max_tokens low enough to truncate the three-outline
        # completion — 0063 verifier finding 1).
        for configured, expected in ((1024, 2048), (4096, 4096), (None, 2048)):
            module, calls = make_pes_module(
                llm_max_tokens=configured, prompt_variant="faithful"
            )
            asyncio.run(module.advise(make_pes_ctx()))
            self.assertEqual(calls[0].get("max_tokens"), expected)

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

    def test_empty_slice_after_heading_logs_and_runs_unplanned(self):
        # Heading present, nothing after it: a truncation symptom shakedown
        # gate 1 must also see (0063 verifier finding 5).
        from noema.coordination.pes.planner import FINAL_PLAN_HEADING

        module, _ = make_pes_module(
            response_text=f"## Plan Outline 1\nA\n{FINAL_PLAN_HEADING}\n   ",
            prompt_variant="faithful",
        )
        with self.assertLogs("noema.coordination.pes.planner", level="WARNING") as logs:
            advice = asyncio.run(module.advise(make_pes_ctx()))
        self.assertTrue(any("empty plan slice" in m for m in logs.output))
        self.assertEqual(advice.prompt_block, "")  # no plan -> default Advice()

    def test_invalid_prompt_variant_rejected(self):
        with self.assertRaises(ValueError):
            make_pes_module(prompt_variant="verbatim")


class TestFaithfulSummaryConstants(unittest.TestCase):
    """pes-faithful summary prompt constants (task 0064, C1).

    Pins the load-bearing recast structure per design note §2: the four brief
    sections and their mandatory checklists, the guidance tags, the kept
    pressure line, the pre-injected sibling section replacing the tool fetch,
    and the absence of tool residue."""

    def test_brief_structure_and_pressure_line_present(self):
        from noema.coordination.pes.summarizer import (
            BRIEF_EXEC_SUMMARY_HEADER,
            BRIEF_GUIDANCE_HEADER,
            FAITHFUL_REFLECTION_MIN_TOKENS,
            FAITHFUL_REFLECTION_SYSTEM,
            FAITHFUL_REFLECTION_USER_TEMPLATE,
        )

        # The four-section brief spec, verbatim.
        for section in (
            "**1. Executive Summary:**",
            "**2. Data-Driven Findings (Facts ONLY):**",
            '**3. Strategic Analysis (The "So What?"):**',
            '**4. Actionable Guidance (The "What\'s Next?"):**',
        ):
            self.assertIn(section, FAITHFUL_REFLECTION_SYSTEM)
        # The headers the host slices the capped downstream slice on must
        # actually occur in the spec the model is told to follow.
        self.assertIn(BRIEF_EXEC_SUMMARY_HEADER, FAITHFUL_REFLECTION_SYSTEM)
        self.assertIn(BRIEF_GUIDANCE_HEADER, FAITHFUL_REFLECTION_SYSTEM)
        # Mandatory checklist variables (X/Y/Z echo the host-computed stats).
        self.assertIn("`**Sibling Rank:**` X out of Y children.", FAITHFUL_REFLECTION_SYSTEM)
        self.assertIn("`**Score Delta:**`", FAITHFUL_REFLECTION_SYSTEM)
        for tag in (
            "Recommend Fusion",
            "Recommend Stripping",
            "Recommend Exploration",
            "Warn",
        ):
            self.assertIn(tag, FAITHFUL_REFLECTION_SYSTEM)
        # Pressure line kept verbatim — it is the treatment.
        self.assertIn("Do not fail in this duty.", FAITHFUL_REFLECTION_SYSTEM)
        # Glossary + the sibling section the tool fetch was recast into.
        self.assertIn("# 1. Data Field Glossary", FAITHFUL_REFLECTION_USER_TEMPLATE)
        self.assertIn("# 4. Sibling Solutions", FAITHFUL_REFLECTION_USER_TEMPLATE)
        self.assertIn("# 4. Sibling Solutions", FAITHFUL_REFLECTION_SYSTEM)
        self.assertIn("system-provided", FAITHFUL_REFLECTION_USER_TEMPLATE)
        for var in (
            "{task_info}",
            "{parent_solution}",
            "{current_solution}",
            "{assessment_result}",
            "{sibling_block}",
        ):
            self.assertIn(var, FAITHFUL_REFLECTION_USER_TEMPLATE)
        self.assertGreaterEqual(FAITHFUL_REFLECTION_MIN_TOKENS, 1024)

    def test_no_tool_residue(self):
        from noema.coordination.pes.summarizer import (
            FAITHFUL_REFLECTION_SYSTEM,
            FAITHFUL_REFLECTION_USER_TEMPLATE,
        )

        both = FAITHFUL_REFLECTION_SYSTEM + FAITHFUL_REFLECTION_USER_TEMPLATE
        for residue in (
            "generate_final_answer",
            "get_childs_by_parent_id",
            "get_parents_by_child_id",
            "Use your tools",
            "human-provided",
        ):
            self.assertNotIn(residue, both)


FAITHFUL_BRIEF = """Here is my analysis of the iteration.

**1. Executive Summary:**
This iteration was a breakthrough because the LP refinement guaranteed validity.

**2. Data-Driven Findings (Facts ONLY):**
* **Sibling Rank:** 1 out of 3.

**3. Strategic Analysis (The "So What?"):**
* **Root Cause:** plan quality.

**4. Actionable Guidance (The "What's Next?"):**
* `Recommend Fusion`: fuse the local-search module into the Delaunay sibling.
"""


def record_child(module, child_id, parent, fitness, plan, outcome_failed=False):
    """Run report_result for one child so it lands in _plans + the queue."""
    child = ProgramView(
        id=child_id,
        code=f"def f():\n    return {fitness}\n",
        fitness=fitness,
        metrics={"score": fitness},
        metadata={"stderr": ""},
    )
    ctx = GenerationContext(
        iteration=0, generation=0, island=0, parent=parent,
        best_fitness_history=[], avg_fitness_history=[],
    )
    module.report_result(
        ctx, child, {"plan": plan, "parent_id": parent.id}, eval_failed=outcome_failed
    )


class TestFaithfulSummaryPath(unittest.TestCase):
    """prompt_variant wiring for the summarizer (task 0064, C2)."""

    def _parent(self):
        return ProgramView(
            id="parent-1", code="def f():\n    return 1\n", fitness=0.5,
            metrics={"score": 0.5},
        )

    def test_custom_default_reflection_prompt_byte_identical_regression(self):
        from noema.coordination.pes.summarizer import (
            REFLECTION_SYSTEM,
            REFLECTION_USER_TEMPLATE,
        )

        module, calls = make_pes_module(response_text="because X")
        parent = self._parent()
        record_child(module, "child-1", parent, 0.7, "# Plan\n\n## Strategy\n- x")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        expected_user = REFLECTION_USER_TEMPLATE.format(
            outcome="improved",
            parent_fitness=0.5,
            child_fitness=0.7,
            error_block="",
            plan="# Plan\n\n## Strategy\n- x",
            parent_code=parent.code,
            child_code="def f():\n    return 0.7\n",
        )
        self.assertEqual(
            calls[0]["messages"],
            [
                {"role": "system", "content": REFLECTION_SYSTEM},
                {"role": "user", "content": expected_user},
            ],
        )
        self.assertNotIn("max_tokens", calls[0])
        # Frozen-hash pin of the custom reflection constants themselves.
        import hashlib

        self.assertEqual(
            hashlib.sha256(REFLECTION_SYSTEM.encode()).hexdigest(),
            CUSTOM_REFLECTION_SYSTEM_SHA,
        )
        self.assertEqual(
            hashlib.sha256(REFLECTION_USER_TEMPLATE.encode()).hexdigest(),
            CUSTOM_REFLECTION_USER_SHA,
        )
        # Custom stores the plain reflection; no full/slice split.
        self.assertEqual(module._plans["child-1"]["reflection"], "because X")
        self.assertNotIn("reflection_full", module._plans["child-1"])

    def test_faithful_sibling_block_stats_for_three_siblings(self):
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful"
        )
        parent = self._parent()
        record_child(module, "child-a", parent, 0.60, "# Plan\n\n## Strategy\n- alpha")
        record_child(module, "child-b", parent, 0.90, "# Plan\n\n## Strategy\n- beta")
        record_child(module, "child-c", parent, 0.75, "# Plan\n\n## Strategy\n- gamma")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        # Three reflect calls; check the last child's prompt (rank 2 of 3).
        user_c = calls[2]["messages"][1]["content"]
        self.assertIn("Total children of this parent (Y): 3", user_c)
        self.assertIn("Current solution's rank by score (X): 2 out of 3", user_c)
        self.assertIn("Top sibling score (Z): 0.9000", user_c)
        # The table lists the family, current row marked, strategies digested.
        self.assertIn("| child-c (current) | 0.7500 | improved | - gamma |", user_c)
        self.assertIn("| child-b | 0.9000 | improved | - beta |", user_c)
        self.assertNotIn("only child", user_c)
        # Assessment line: label + delta, host-computed.
        self.assertIn("IMPROVED: fitness 0.5000 -> 0.7500 (score delta +0.2500)", user_c)
        self.assertEqual(calls[2]["max_tokens"], 1024)


    def test_faithful_only_child_note(self):
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful"
        )
        record_child(module, "child-a", self._parent(), 0.6, "plan text")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        user = calls[0]["messages"][1]["content"]
        self.assertIn("Current solution's rank by score (X): 1 out of 1", user)
        self.assertIn("its rank is 1 out of 1", user)

    def test_faithful_storage_split_and_capped_slice(self):
        module, _ = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful"
        )
        record_child(module, "child-a", self._parent(), 0.6, "plan text")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        entry = module._plans["child-a"]
        # Full brief stays in module state...
        self.assertEqual(entry["reflection_full"], FAITHFUL_BRIEF.strip())
        # ...only Executive Summary + Actionable Guidance re-enter prompts,
        # with the model's preamble stripped and the middle sections dropped.
        slice_text = entry["reflection"]
        self.assertIn("**1. Executive Summary:**", slice_text)
        self.assertIn("**4. Actionable Guidance", slice_text)
        self.assertNotIn("**2. Data-Driven Findings", slice_text)
        self.assertNotIn("**3. Strategic Analysis", slice_text)
        self.assertNotIn("Here is my analysis", slice_text)
        self.assertLessEqual(len(slice_text), module.reflection_slice_max_tokens * 4 + 20)

    def test_capped_slice_is_what_downstream_prompts_reinject(self):
        # The binding context-protection rule: later planner prompts carry the
        # capped slice, never the full brief (design note §2.3(a)).
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful"
        )
        parent = self._parent()
        record_child(module, "child-a", parent, 0.6, "plan text")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        child_view = ProgramView(
            id="child-a", code="def f():\n    return 0.6\n", fitness=0.6,
            metrics={"score": 0.6},
        )
        asyncio.run(module.advise(make_pes_ctx(parent=child_view)))
        planner_user = calls[-1]["messages"][1]["content"]
        self.assertIn("**1. Executive Summary:**", planner_user)
        self.assertNotIn("**2. Data-Driven Findings", planner_user)
        self.assertNotIn("Here is my analysis", planner_user)

    def test_sibling_block_degenerates_to_only_child_without_parent_id(self):
        # A GENESIS/blank parent id, or a queue entry checkpointed before
        # parent_id existed, must not render "0 out of 0" stats the model is
        # told to copy verbatim (0064 verifier finding 1).
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful"
        )
        module._plans["child-a"] = {
            "plan": "plan text",
            "outcome": "improved",
            "parent_fitness": 0.5,
            "child_fitness": 0.6,
        }  # legacy entry: no parent_id
        module._pending_reflections.append(
            {  # legacy queue entry: no parent_id, no metrics
                "child_id": "child-a",
                "plan": "plan text",
                "outcome": "improved",
                "parent_fitness": 0.5,
                "child_fitness": 0.6,
                "parent_code": "def f():\n    return 1\n",
                "child_code": "def f():\n    return 0.6\n",
                "stderr": "",
            }
        )
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        user = calls[0]["messages"][1]["content"]
        self.assertIn("Total children of this parent (Y): 1", user)
        self.assertIn("Current solution's rank by score (X): 1 out of 1", user)
        self.assertIn("Top sibling score (Z): 0.6000", user)
        self.assertNotIn("0 out of 0", user)
        self.assertIn("| child-a (current) | 0.6000 |", user)

    def test_strategy_digest_escapes_pipes(self):
        # An unescaped pipe would add phantom columns to the stats table the
        # model copies from (0064 verifier finding 2).
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful"
        )
        record_child(
            module,
            "child-a",
            self._parent(),
            0.6,
            "# Plan\n\n## Strategy\n- pipe | in | plan",
        )
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        user = calls[0]["messages"][1]["content"]
        self.assertIn("- pipe \\| in \\| plan |", user)

    def test_downstream_slice_truncates_over_cap(self):
        # Exercises the truncation branch itself: the cap is in CHARS
        # (tokens * 4), so an over-cap brief must actually be cut. Without
        # this the cap could be wrong by 4x and every test still passes
        # (0064 verifier finding 3).
        long_guidance = "x" * 900
        brief = (
            "**1. Executive Summary:**\nshort.\n\n"
            "**2. Data-Driven Findings (Facts ONLY):**\ndropped.\n\n"
            '**4. Actionable Guidance (The "What\'s Next?"):**\n' + long_guidance + "\n"
        )
        module, _ = make_pes_module(
            response_text=brief,
            prompt_variant="faithful",
            reflection_slice_max_tokens=100,  # cap = 400 chars
        )
        record_child(module, "child-a", self._parent(), 0.6, "plan text")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        entry = module._plans["child-a"]
        slice_text = entry["reflection"]
        self.assertLess(len(slice_text), len(entry["reflection_full"]))
        self.assertTrue(slice_text.endswith("... (truncated)"))
        self.assertLessEqual(len(slice_text), 400 + len("\n... (truncated)"))

    def test_downstream_slice_strips_preamble_on_fallback(self):
        # When the model malforms the section headers the slice falls back to
        # the whole brief — but the preamble strip must still fire, so chatty
        # lead-ins never re-enter later prompts (design note §2.3(c);
        # 0064 verifier finding 4: the happy path alone does not pin this).
        brief = (
            "Sure! Here is my analysis.\n\n"
            "**1. Executive Summary**\nno colon, so no header match.\n"
        )
        module, _ = make_pes_module(response_text=brief, prompt_variant="faithful")
        record_child(module, "child-a", self._parent(), 0.6, "plan text")
        asyncio.run(module.on_generation_end(make_pes_ctx()))
        slice_text = module._plans["child-a"]["reflection"]
        self.assertTrue(slice_text.startswith("**1. Executive Summary**"))
        self.assertNotIn("Sure! Here is my analysis", slice_text)

    def test_prompt_size_assertion_fails_loud_before_dispatch(self):
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF,
            prompt_variant="faithful",
            context_window_tokens=1500,  # smaller than prompt + 1024 reserve
        )
        record_child(module, "child-a", self._parent(), 0.6, "plan text")
        with self.assertRaises(ValueError) as cm:
            asyncio.run(module.on_generation_end(make_pes_ctx()))
        self.assertIn("overflow the context window", str(cm.exception))
        self.assertEqual(calls, [])  # nothing was dispatched

    # ----------------------------------------- task 0067: context-fit regression

    def _family(self, module, n, parent=None):
        """Record n children of ONE parent, so they are siblings of each other.

        Uses REALISTIC content sizes — the live run's programs were real circle-
        packing constructors, not toy stubs. Code and stderr are stored at the
        max_code_chars cap (the worst case the host itself permits), because that
        is the condition the arm has to survive: every bounded field at its bound.
        """
        parent = parent or ProgramView(
            id="parent-1",
            code="# parent\n" + "x = 1\n" * 400,  # ~2.4k chars, capped to 2000
            fitness=0.5,
            metrics={"score": 0.5},
        )
        for i in range(n):
            child = ProgramView(
                id=f"child-{i:03d}",
                code=f"# child {i}\n" + "y = 2\n" * 400,
                fitness=0.5 + i / 1000.0,
                metrics={"score": 0.5 + i / 1000.0},
                metadata={"stderr": "Traceback:\n" + "  frame\n" * 200},
            )
            ctx = GenerationContext(
                iteration=i, generation=0, island=0, parent=parent,
                best_fitness_history=[], avg_fitness_history=[],
            )
            module.report_result(
                ctx, child, {"plan": f"plan {i}: " + "detail " * 60,
                             "parent_id": parent.id}, eval_failed=False,
            )
        return parent

    def test_sibling_table_growth_overflows_the_window(self):
        # Task 0067. The 2026-07-13 live run died at 9% of budget with
        # "~6268 prompt tokens + 4096 reserved > 10240". This reproduces the
        # cause: EVERY component of the faithful reflection prompt is bounded
        # (code and stderr by max_code_chars, the parent's brief by the 300-token
        # downstream slice) EXCEPT the sibling table, which renders one row per
        # child of the parent with no cap. The prompt therefore grows without
        # limit as a parent accumulates children, and a run that fits early
        # fails later — the arm is a time bomb, not merely mis-pinned.
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF,
            prompt_variant="faithful",
            llm_max_tokens=4096,  # the live run's LLMClientConfig.max_tokens
            context_window_tokens=10240,  # the value the failed run actually ran with
            max_siblings_rendered=None,  # unbounded: the pre-0067 behaviour
        )
        parent = self._family(module, 60)
        with self.assertRaises(ValueError) as cm:
            asyncio.run(module.on_generation_end(make_pes_ctx(parent=parent)))
        self.assertIn("overflow the context window", str(cm.exception))
        self.assertEqual(calls, [])  # fail-loud, nothing dispatched

    def test_sibling_table_is_capped_so_a_large_family_still_fits(self):
        # The fix: the sibling TABLE is capped (a host-added field, not donor
        # text). The same 60-child family now fits and the call is dispatched.
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF,
            prompt_variant="faithful",
            llm_max_tokens=4096,
            context_window_tokens=10240,
        )
        parent = self._family(module, 60)
        asyncio.run(module.on_generation_end(make_pes_ctx(parent=parent)))
        self.assertTrue(calls, "reflection should have been dispatched")

    def test_capped_table_still_reports_the_TRUE_family_size_and_rank(self):
        # The cap must not corrupt the treatment. X/Y/Z (rank, total, top score)
        # are computed over the WHOLE family and are what the model is told to
        # copy into its checklist; only the rendered ROWS are limited. Reporting
        # a truncated Y would silently change what the mechanism tells the model.
        module, _ = make_pes_module(
            response_text=FAITHFUL_BRIEF, prompt_variant="faithful", max_siblings_rendered=5
        )
        parent = self._family(module, 40)
        block = module._summarizer._sibling_block(
            {"child_id": "child-000", "parent_id": parent.id}
        )
        self.assertIn("Total children of this parent (Y): 40", block)
        self.assertIn("out of 40", block)  # rank is over the true family
        rows = [ln for ln in block.splitlines() if ln.startswith("| child-")]
        self.assertEqual(len(rows), 5)  # but only 5 rows rendered
        self.assertIn("child-000", block)  # the current child is always shown

    def test_genuinely_oversized_prompt_still_fails_loud_after_the_cap(self):
        # The guard is NOT weakened by the cap. Capping the sibling table bounds
        # the one unbounded field; it must not become a licence to truncate the
        # rest. A single child whose *bounded* fields are configured huge
        # (max_code_chars far above the study value) still overflows the real
        # 16384 window, and must still fail loudly rather than be silently cut.
        module, calls = make_pes_module(
            response_text=FAITHFUL_BRIEF,
            prompt_variant="faithful",
            llm_max_tokens=4096,
            context_window_tokens=16384,  # the REAL server window
            max_code_chars=60_000,  # ~15k tokens of code alone
        )
        parent = ProgramView(
            id="parent-1",
            code="# huge\n" + "x = 1\n" * 12_000,
            fitness=0.5,
            metrics={"score": 0.5},
        )
        child = ProgramView(
            id="child-a",
            code="# huge\n" + "y = 2\n" * 12_000,
            fitness=0.6,
            metrics={"score": 0.6},
            metadata={"stderr": ""},
        )
        ctx = GenerationContext(
            iteration=0, generation=0, island=0, parent=parent,
            best_fitness_history=[], avg_fitness_history=[],
        )
        module.report_result(
            ctx, child, {"plan": "plan text", "parent_id": parent.id}, eval_failed=False
        )
        with self.assertRaises(ValueError) as cm:
            asyncio.run(module.on_generation_end(make_pes_ctx(parent=parent)))
        self.assertIn("overflow the context window", str(cm.exception))
        self.assertEqual(calls, [])  # fail-loud, nothing dispatched

    def test_faithful_constants_byte_pinned(self):
        # The KEEP lines ARE the fidelity claim (a recorded verbatim diff is
        # not machine-checked); pin all four faithful constants by hash so an
        # edit inside them can't drift silently (0064 verifier finding 5).
        import hashlib

        from noema.coordination.pes.planner import (
            FAITHFUL_PLANNER_SYSTEM,
            FAITHFUL_PLANNER_USER_TEMPLATE,
        )
        from noema.coordination.pes.summarizer import (
            FAITHFUL_REFLECTION_SYSTEM,
            FAITHFUL_REFLECTION_USER_TEMPLATE,
        )

        expected = {
            FAITHFUL_PLANNER_SYSTEM: (
                "e163b2371d1203832ff982a0147275286def177e2c371b128e88152d38378693"
            ),
            FAITHFUL_PLANNER_USER_TEMPLATE: (
                "cb1c0356e194a4c4936ed129cc247940012edc26ea1a028b9a59a05e07193b0e"
            ),
            FAITHFUL_REFLECTION_SYSTEM: (
                "727f904b7f089f687a6d7121e803ebbfd335ab98c459e9728975123ce99c49ae"
            ),
            FAITHFUL_REFLECTION_USER_TEMPLATE: (
                "545b7202de4bf929bd5600382bdd63382667f8bd81486596b6d08565f7e2e83b"
            ),
        }
        for constant, sha in expected.items():
            self.assertEqual(hashlib.sha256(constant.encode()).hexdigest(), sha)


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
class TestPESExecutorDirectiveConstants(unittest.TestCase):
    """Regression pin for the verbatim LoongFlow executor prompt constants
    (task 0065, BORROWED CODE header in noema/coordination/pes/executor.py)."""

    def test_executor_system_with_plan_is_pinned(self):
        from noema.coordination.pes.executor import EXECUTOR_SYSTEM_WITH_PLAN

        self.assertIn("expert software developer", EXECUTOR_SYSTEM_WITH_PLAN)
        self.assertIn("generation plan", EXECUTOR_SYSTEM_WITH_PLAN)

    def test_executor_user_with_plan_is_pinned(self):
        from noema.coordination.pes.executor import EXECUTOR_USER_WITH_PLAN

        for marker in (
            "{task}",
            "{plan}",
            "{parent_solution}",
            "{previous_attempts}",
            "# Task Information",
            "# Plan",
            "# Parent Solution",
            "# Previous Iteration Attempts",
            "# Requirement",
            "```python",
        ):
            self.assertIn(marker, EXECUTOR_USER_WITH_PLAN)

    def test_advisory_mode_never_formats_the_directive_template(self):
        # Regression guard for the executor_mode="advisory" default (task 0065):
        # the directive template's section headers must never leak into the
        # advisory suffix, which is just the raw plan text.
        from noema.coordination.pes.executor import Executor
        from noema.coordination.base import GenerationContext
        from noema.substrate.views import ProgramView
        from types import SimpleNamespace

        module = SimpleNamespace(executor_mode="advisory", _plans={}, domain_context="")
        executor = Executor(module)
        ctx = GenerationContext(
            iteration=0, generation=0, island=0,
            parent=ProgramView(id="p", code="def f(): pass", fitness=0.5, metrics={}),
        )
        advice = executor.build_advice("my plan", ctx)
        self.assertEqual(advice.prompt_block, "my plan")
        self.assertEqual(advice.system_block, "")
        self.assertNotIn("full_executor_prompt", advice.attribution)


class TestPromptIdentityDecision25Exemption(unittest.TestCase):
    """Prompt-identity guarantee, re-based per Decision #25 (Decisions.md,
    user 2026-07-10): the shared mutation-prompt prefix is asserted
    byte-identical across every arm EXCEPT the declared pes-faithful fidelity
    anchor in executor_mode="directive" — and even there, the exemption's
    exact scope is checked: only that one arm, only in directive mode, and
    no other arm can trip the same attribution flag.

    (s1 is named in Decision #25 but is not a registered coordination arm;
    this test covers the prompt-producing arms relevant to the exemption.)
    """

    def _shared_prefix_prompt(self, advice):
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        base = build(sampler, make_parent())
        return inject_advice(base, advice.prompt_block, advice.system_block), base

    def test_null_and_hifo_and_pes_advisory_share_the_prefix(self):
        from noema.coordination.base import NullCoordination
        from noema.coordination.hifo.module import HiFoPromptModule

        ctx = GenerationContext(
            iteration=0, generation=0, island=0,
            parent=ProgramView(id="p", code="def f():\n    return 1\n", fitness=0.5, metrics={}),
            best_fitness_history=[0.1], avg_fitness_history=[0.1],
        )
        # PES advisory is driven through a real advise() with a fake LLM, so a
        # NON-EMPTY advice block is what gets injected — substituting Advice()
        # here would assert on the fixture, not on the arm.
        pes_advisory, _ = make_pes_module(
            response_text="# Plan\n\n## Strategy\n- x", executor_mode="advisory"
        )
        for module in (NullCoordination(), HiFoPromptModule(), pes_advisory):
            advice = asyncio.run(module.advise(ctx))
            injected, base = self._shared_prefix_prompt(advice)
            self.assertTrue(injected["user"].startswith(base["user"]))
            self.assertTrue(injected["system"].startswith(base["system"]))
            self.assertNotIn("full_executor_prompt", advice.attribution)
        # The PES advisory arm really did produce a plan block (i.e. the
        # prefix-sharing above was tested against a non-trivial suffix).
        pes_advice = asyncio.run(pes_advisory.advise(ctx))
        self.assertIn("## Strategy", pes_advice.prompt_block)

    def test_only_pes_directive_sets_full_executor_prompt(self):
        from noema.budget.ledger import TokenLedger
        from noema.budget.llm import BudgetedLLM
        from noema.coordination.pes.module import PESPlannerModule
        from noema.coordination.pes.executor import (
            EXECUTOR_SYSTEM_WITH_PLAN,
            EXECUTOR_USER_WITH_PLAN,
        )
        from types import SimpleNamespace

        async def create(**params):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="# Plan\nfoo"))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        llm = BudgetedLLM(
            model="fake-model", ledger=TokenLedger(total_budget_tokens=100_000),
            account="coordination", tag="pes.coordination", client=client, retries=0, retry_delay=0.0,
        )
        ctx = GenerationContext(
            iteration=0, generation=0, island=0,
            parent=ProgramView(id="p", code="def f():\n    return 1\n", fitness=0.5, metrics={}),
            best_fitness_history=[0.1], avg_fitness_history=[0.1],
        )

        advisory = PESPlannerModule(config={"executor_mode": "advisory"}, llm=llm)
        advisory_advice = asyncio.run(advisory.advise(ctx))
        self.assertNotIn("full_executor_prompt", advisory_advice.attribution)

        directive = PESPlannerModule(config={"executor_mode": "directive"}, llm=llm)
        directive_advice = asyncio.run(directive.advise(ctx))
        self.assertIs(directive_advice.attribution["full_executor_prompt"], True)

        # Exemption's exact scope: the directive prompt matches the verbatim
        # template exactly (not a shared-prefix + suffix construction)
        self.assertEqual(directive_advice.system_block, EXECUTOR_SYSTEM_WITH_PLAN)
        self.assertNotEqual(directive_advice.prompt_block, EXECUTOR_USER_WITH_PLAN)  # formatted
        for section in ("{task}", "{plan}", "{parent_solution}", "{previous_attempts}"):
            self.assertNotIn(section, directive_advice.prompt_block)  # placeholders all filled


if __name__ == "__main__":
    unittest.main()
