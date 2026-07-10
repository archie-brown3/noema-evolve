"""
Plan phase of the PES arm (LoongFlow: agents/general_agent/planner.py).

Extracted from module.py (task 0060, behavior-identical split). The
PESPlannerModule façade owns all shared state (_plans, the reflection queue,
config knobs, llm) and hands itself to the phase object by reference.
"""

import logging
from typing import TYPE_CHECKING, List, Optional

from noema.budget.ledger import BudgetExhausted
from noema.coordination.base import GenerationContext

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from noema.coordination.pes.module import PESPlannerModule

logger = logging.getLogger(__name__)

# =============================================================================
# BORROWED CODE — prompt text adapted from LoongFlow (Apache-2.0)
# Source: https://github.com/baidu-baige/LoongFlow
#         src/loongflow/framework/claude_code/general_prompt.py
#         (GENERAL_PLANNER_SYSTEM lines 27-79, GENERAL_PLANNER_USER lines
#         82-183; local clone /home/archie/LoongFlow)
# Condensed for a single-call recast; structural skeleton (Situation Analysis /
# Strategy / Action Steps / Success Criteria) kept verbatim from the mandated
# plan structure. Local changes marked NOEMA.
# =============================================================================

PLANNER_SYSTEM = """You are a strategic planner in a structured problem-solving system.
Design a clear, actionable plan that guides the next code mutation to improve
from the current solution (parent) to a better solution (child).

Key principles:
- Be specific: vague plans lead to vague results. State exactly what should be done.
- Be actionable: the implementer must understand precisely what steps to take.
- Learn from history: avoid repeating approaches that already failed.
- Stay focused: every plan element should directly serve the objective."""
# NOEMA: condensed from GENERAL_PLANNER_SYSTEM; PES-cycle framing and
# tool/skill instructions dropped (no tools in a single-call recast)

PLANNER_USER_TEMPLATE = """# Task Objective
Improve the program's fitness score through one targeted mutation.

# Current Solution (parent)
- Fitness: {fitness:.4f}
- Metrics: {metrics}

```
{code}
```

# Prior Plan For This Solution
{prior_block}
{recent_block}
# Population Status
- Recent best-fitness history: {best_history}
- Recent average-fitness history: {avg_history}

# Your Mission
Design a plan for the next mutation of this program.

If improving on a prior plan: identify what worked and what didn't based on its
outcome, design targeted improvements, and avoid repeating approaches that
already failed. If no prior plan exists, design a strategy from scratch.

Your plan MUST follow this exact structure (keep each section to 1-3 short
bullets; output ONLY the plan):

# Plan

## Situation Analysis
[current state: core problem, what the prior plan's outcome tells us, risks]

## Strategy
[chosen approach and why it suits this program]

## Action Steps
[numbered, specific steps the mutation should take]

## Success Criteria
[what metrics or evidence indicate the mutation succeeded]"""
# NOEMA: condensed from GENERAL_PLANNER_USER; solution-pack/manifest and
# workspace/skills sections dropped (single-file programs, no tools);
# "Expected Deliverables" section dropped (deliverable fixed by substrate);
# brevity constraint added (plan is a prompt suffix, not a standalone file);
# {recent_block} is a noema-original field (deviation #6) — no LoongFlow analog.

# ============================== END BORROWED =================================

_HISTORY_TAIL = 5  # recent history entries shown to the planner


class Planner:
    """Plan phase: builds the planning prompt and makes the one metered
    `pes.plan` call per mutation. Shared state lives on the module façade."""

    def __init__(self, module: "PESPlannerModule"):
        self._m = module

    async def plan(self, ctx: GenerationContext) -> Optional[str]:
        """One metered `pes.plan` call. Returns the stripped plan text, or
        None when the call failed or produced nothing (the iteration then
        runs unplanned). BudgetExhausted propagates (clean run stop)."""
        m = self._m
        prompt = self._build_planning_prompt(ctx)
        system_message = PLANNER_SYSTEM
        if m.domain_context:
            system_message = f"{PLANNER_SYSTEM}\n\n# Problem Domain\n{m.domain_context}"
        try:
            plan = await m.llm.generate_with_context(
                system_message=system_message,
                messages=[{"role": "user", "content": prompt}],
                tag="pes.plan",
            )
        except BudgetExhausted:
            raise  # clean run stop, same contract as the mutation account
        except Exception as e:
            logger.warning(f"PES planning call failed; iteration runs unplanned: {e}")
            return None
        plan = (plan or "").strip()
        return plan or None

    def _build_planning_prompt(self, ctx: GenerationContext) -> str:
        m = self._m
        parent = ctx.parent
        prior = m._plans.get(parent.id)
        if prior:
            prior_block = (
                f"Outcome of the plan that produced this solution: **{prior['outcome']}** "
                f"(fitness {prior['parent_fitness']:.4f} -> {prior['child_fitness']:.4f})\n\n"
                f"{prior['plan']}"
            )
            # Reflection (Phase 2) on that outcome, when available — the causal
            # "why it worked/failed" that the deferred summary call produced.
            reflection = prior.get("reflection")
            if reflection:
                prior_block += f"\n\n## Reflection on that outcome\n{reflection}"
        else:
            prior_block = "None — first plan for this lineage."

        return PLANNER_USER_TEMPLATE.format(
            fitness=parent.fitness,
            metrics={k: v for k, v in parent.metrics.items() if isinstance(v, (int, float))},
            code=m._truncate(parent.code),
            prior_block=prior_block,
            recent_block=self._recent_strategies_block(exclude_id=parent.id),
            best_history=[round(v, 4) for v in ctx.best_fitness_history[-_HISTORY_TAIL:]],
            avg_history=[round(v, 4) for v in ctx.avg_fitness_history[-_HISTORY_TAIL:]],
        )

    # -------------------------------------------- cross-lineage diversity (D2)

    @staticmethod
    def _extract_strategy(plan_text: str) -> str:
        """Pull the `## Strategy` section body out of a stored plan (or '')."""
        marker = "## Strategy"
        start = plan_text.find(marker)
        if start < 0:
            return ""
        rest = plan_text[start + len(marker):]
        end = rest.find("\n##")
        section = rest[:end] if end >= 0 else rest
        return " ".join(section.split()).strip()

    def _recent_strategies_block(self, exclude_id: Optional[str] = None) -> str:
        """
        A population-wide, cross-lineage digest of recently-attempted strategies
        and their outcomes — noema-original (deviation #6). Built from the
        module's _plans (flat across all islands/lineages, insertion-ordered =
        iteration-ordered), so a fresh lineage's first plan still sees what
        other islands already tried and failed. No LLM call: plain truncation
        of the `## Strategy` section (D4). Returns "" when there's nothing to
        show yet.
        """
        m = self._m
        if m.recent_strategies_k <= 0:
            return ""
        seen = set()
        lines: List[str] = []
        for cid, entry in reversed(m._plans.items()):
            if cid == exclude_id:
                continue  # the lineage's own last plan is already in prior_block
            strategy = self._extract_strategy(entry.get("plan", ""))
            if not strategy:
                continue
            digest = strategy[: m.strategy_digest_chars].strip()
            key = digest.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{entry.get('outcome', '?')}] {digest}")
            if len(lines) >= m.recent_strategies_k:
                break
        if not lines:
            return ""
        return (
            "\n# Recently Attempted Elsewhere\n"
            "Strategies already tried across the population — avoid repeating the "
            "failed ones, and prefer a distinct approach:\n" + "\n".join(lines) + "\n"
        )
