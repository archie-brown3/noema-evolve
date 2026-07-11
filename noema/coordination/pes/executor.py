"""
Execute phase of the PES arm (LoongFlow: agents/general_agent/executor.py).

Extracted from module.py (task 0060, behavior-identical split). Advisory mode
wraps the plan into the standard coordination `Advice` suffix (unchanged,
byte-identical); directive mode (task 0065) makes the plan the mutation
call's primary instruction via the verbatim LoongFlow executor prompt below.
"""

import json
from typing import TYPE_CHECKING, Dict

from noema.coordination.base import Advice, GenerationContext

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from noema.coordination.pes.module import PESPlannerModule

# =============================================================================
# BORROWED CODE — executor prompt adapted from LoongFlow (Apache-2.0)
# Source: https://github.com/baidu-baige/LoongFlow
#         agents/math_agent/prompt/evolve_execute_prompt.py
#         (EVOLVE_EXECUTOR_CHAT_SYSTEM_PROMPT_WITH_PLAN lines 104-107,
#          EVOLVE_EXECUTOR_CHAT_USER_PROMPT_WITH_PLAN lines 109-143)
# Portable as-is: plain LLM call variant (not ReAct, not agentic) — matches
# noema's single-call mutation shape exactly. VERBATIM: both constants were
# diffed line-by-line against the upstream file (difflib, 0 differing lines,
# byte-identical) — no ADAPTs were needed, since neither prompt references a
# tool, a file, or the workspace. The plan is the executor's brief here exactly
# as it is upstream: this is the Decision #25 scoped prompt-identity exemption.
# Local changes (none in the text; only the host-side {previous_attempts} fill
# format) marked NOEMA.
# =============================================================================

EXECUTOR_SYSTEM_WITH_PLAN = """You are an expert software developer tasked with iteratively improving a codebase.
Your job is to analyze the parent solution and suggest improvements based on feedback from generation plan.
Focus on making targeted changes that will increase the solution's evaluation score and complete the task objectives.
"""

EXECUTOR_USER_WITH_PLAN = """# Task Information
{task}

# Plan
{plan}

# Parent Solution
{parent_solution}

## Filed Description
- generate_plan: This is the generation plan that guides the generation of this parent solution.
- solution: This is the real solution content.
- score: A quantitative measure of a solution's fitness (completion ratio). A score of `1.0` or greater means the task objective is met.
- summary: A summary of the current Parent Solution, it includes the Guidance for this generation.

# Previous Iteration Attempts
{previous_attempts}

## How to use it?
1. If the evaluation failed, read the error message, find out why it failed based on the generation plan and fix it in this re-written solution.
2. If the evaluation succeeded, but the solution's evaluation score < 1.0, find out why it is not finish the task objective, and fix it in this re-written solution.

# Requirement
1. Rewrite the solution to improve the evaluation score.
2. Provide the complete new child solution without syntax errors.
3. Fully understand the task and the generation plan, and generate a new child solution to finish the task objective.

IMPORTANT: Make sure your rewritten child solution maintains the same input and output as the parent solution, but with improved internal implementation.
VERY IMPORTANT: You MUST generate the FULL child solution, not a diff or partial solution.
VERY VERY IMPORTANT: This is your last chance, you must generate a child solution that can get evaluation score >= 1.0.

```python
# Your rewritten program here.
```
"""

# ============================== END BORROWED =================================


class Executor:
    """Execute phase: turns a plan into the mutation call's coordination
    input. Shared state lives on the module façade."""

    def __init__(self, module: "PESPlannerModule"):
        self._m = module
        # Directive-mode retry state (task 0065): reset on every build_advice
        # call (one per mutation); accumulated across that mutation's retries.
        self._directive_task: str = ""
        self._directive_plan: str = ""
        self._directive_parent_solution: str = ""
        self._directive_previous_attempts: str = ""

    def build_advice(self, plan: str, ctx: GenerationContext) -> Advice:
        """Dispatches on `executor_mode`. Advisory (default): the plan rides
        as the standard coordination suffix, byte-identical to today.
        Directive: the verbatim LoongFlow executor prompt, plan as the
        primary instruction (Decision #25 scoped exemption)."""
        if self._m.executor_mode == "directive":
            return self._build_directive_advice(plan, ctx)
        return Advice(
            prompt_block=plan,
            attribution={"plan": plan, "parent_id": ctx.parent.id},
        )

    def _build_directive_advice(self, plan: str, ctx: GenerationContext) -> Advice:
        prior = self._m._plans.get(ctx.parent.id)
        # NOEMA: "reflection" IS the capped slice as of task 0064 — the full
        # faithful brief lives under "reflection_full" and must never enter a
        # prompt (design note §2.3(a); the locked context window is binding).
        summary = prior.get("reflection", "") if prior else ""
        parent_solution = json.dumps(
            {
                "generate_plan": prior.get("plan", "") if prior else "",
                "solution": ctx.parent.code,
                "score": ctx.parent.fitness,
                "summary": summary,
            }
        )
        self._directive_task = self._m.domain_context or ""
        self._directive_plan = plan
        self._directive_parent_solution = parent_solution
        self._directive_previous_attempts = ""
        user_prompt = EXECUTOR_USER_WITH_PLAN.format(
            task=self._directive_task,
            plan=self._directive_plan,
            parent_solution=self._directive_parent_solution,
            previous_attempts=self._directive_previous_attempts,
        )
        return Advice(
            prompt_block=user_prompt,
            system_block=EXECUTOR_SYSTEM_WITH_PLAN,
            attribution={
                "plan": plan,
                "parent_id": ctx.parent.id,
                "full_executor_prompt": True,
            },
        )

    def retry_block(self, ctx: GenerationContext, error_text: str, attempt: int) -> str:
        """Seed a Stage-1 retry with this lineage's causal reflection (Design 4).

        Returns the stored reflection text (the "why it failed" from the deferred
        summary call) framed as a retry-guidance block, or "" when there's no
        parent or no reflection yet (fresh lineage). The controller concatenates
        this after its raw-error suffix; Null inherits the no-op, so only PES
        retries carry reflection — the controlled variable stays single.

        Directive mode yields "" here: its retries re-format the full LoongFlow
        template via `retry_prompt` instead (LoongFlow retries carry evaluation
        text, not reflections — see `retry_prompt`).
        """
        if self._m.executor_mode == "directive":
            return ""
        if ctx.parent is None:
            return ""
        prior = self._m._plans.get(ctx.parent.id)
        reflection = prior.get("reflection") if prior else None
        if not reflection:
            return ""
        return (
            "\n# Reflection on the lineage's last failure\n"
            f"{reflection}\n"
            "Use this causal explanation to guide the corrected mutation."
        )

    def retry_prompt(self, attempt: int, error_or_eval_text: str) -> Dict[str, str]:
        """Directive-mode retry (task 0065): re-format the FULL LoongFlow
        template with `{previous_attempts}` accumulated in LoongFlow's exact
        `Round {i}, Candidate 0, Evaluation Result: {reason}` format
        (execute_agent_chat.py:167-172). Works for both 0062 retry triggers:
        `retry_on="failure"` passes the raw error text, `"non_improvement"`
        passes the evaluation summary. Round = the controller's attempt index,
        candidate = 0 (noema mapping, no multi-candidate rounds)."""
        self._directive_previous_attempts += (
            f"Round {attempt}, Candidate 0, Evaluation Result: {error_or_eval_text}\n\n"
        )
        user_prompt = EXECUTOR_USER_WITH_PLAN.format(
            task=self._directive_task,
            plan=self._directive_plan,
            parent_solution=self._directive_parent_solution,
            previous_attempts=self._directive_previous_attempts,
        )
        return {"system": EXECUTOR_SYSTEM_WITH_PLAN, "user": user_prompt}
