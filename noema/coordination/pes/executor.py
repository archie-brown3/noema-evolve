"""
Execute phase of the PES arm (LoongFlow: agents/general_agent/executor.py).

Extracted from module.py (task 0060, behavior-identical split). Today the
executor wraps the plan into the standard coordination `Advice` suffix
(advisory mode); the directive mode — the full LoongFlow executor prompt as
the mutation call's primary instruction — lands in task 0065.
"""

from typing import TYPE_CHECKING

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

    def build_advice(self, plan: str, ctx: GenerationContext) -> Advice:
        """Advisory mode: the plan rides as the standard coordination suffix,
        with the plan + parent id recorded in attribution for report_result."""
        return Advice(
            prompt_block=plan,
            attribution={"plan": plan, "parent_id": ctx.parent.id},
        )

    def retry_block(self, ctx: GenerationContext, error_text: str, attempt: int) -> str:
        """Seed a Stage-1 retry with this lineage's causal reflection (Design 4).

        Returns the stored reflection text (the "why it failed" from the deferred
        summary call) framed as a retry-guidance block, or "" when there's no
        parent or no reflection yet (fresh lineage). The controller concatenates
        this after its raw-error suffix; Null inherits the no-op, so only PES
        retries carry reflection — the controlled variable stays single.
        """
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
