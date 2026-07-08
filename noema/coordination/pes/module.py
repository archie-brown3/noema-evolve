"""
PES planner coordination arm (Phase 1 of the LoongFlow transplant).

Mapping from the released LoongFlow code (vault: "LoongFlow Fit Assessment"):
- advise()        <- the Planner phase: one metered planning call per mutation,
                     plan injected as the standard coordination suffix
                     (LoongFlow: agents/general_agent/planner.py)
- report_result() <- the Summary phase's *computed* assessment only
                     (IMPROVEMENT/REGRESSION/STALE vs parent score,
                     agents/general_agent/summary.py:233-247); plan + outcome
                     stored per child so the next plan for that lineage sees
                     them (LoongFlow stores generate_plan/summary on every
                     solution: framework/pes/database/database.py:96-101)

Documented deviations from the released code (PLAN.md section 2.2 discipline):
1. One single-shot planning call, not a multi-turn Claude Code agent session.
   LoongFlow's own math_agent runs PES on plain LLM calls, so this recast has
   upstream precedent.
2. The plan is a prompt *suffix* after openevolve's mutation instructions, not
   the executor's primary directive. Biggest fidelity gap — flagged in the fit
   assessment; accepted for Phase 1.
3. The parent is sampled by the host and handed in via GenerationContext;
   LoongFlow's planner samples it itself and can query the database with
   tools. Lineage memory here is the module-internal plan/outcome store.
4. Phase 1 makes NO summary LLM call — outcome classification is computed
   from fitness only. The reflective summarizer is Phase 2.
5. "Expected Deliverables" is dropped from the plan skeleton: the deliverable
   is fixed by the substrate (a diff/rewrite of the program).
"""

import logging
from typing import Any, Dict, Optional

from noema.budget.ledger import BudgetExhausted
from noema.coordination.base import Advice, CoordinationModule, GenerationContext
from noema.substrate.views import ProgramView

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
# brevity constraint added (plan is a prompt suffix, not a standalone file)

# ============================== END BORROWED =================================

# Outcome labels mirror LoongFlow's Assessment enum (summary.py:233-247)
IMPROVED = "improved"
REGRESSED = "regressed"
STALE = "stale"
FAILED = "failed"

_HISTORY_TAIL = 5  # recent history entries shown to the planner


class PESPlannerModule(CoordinationModule):
    """LoongFlow-derived planner arm: plan-before-mutate, assess-after."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        llm=None,
        rng=None,
    ):
        super().__init__(config=config, llm=llm, rng=rng)
        self.max_code_chars: int = self.config.get("max_code_chars", 2000)
        # plan/outcome per child program id — the PES lineage memory
        self._plans: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------- advise

    async def advise(self, ctx: GenerationContext) -> Advice:
        if ctx.parent is None or self.llm is None:
            return Advice()

        prompt = self._build_planning_prompt(ctx)
        try:
            plan = await self.llm.generate_with_context(
                system_message=PLANNER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tag="pes.plan",
            )
        except BudgetExhausted:
            raise  # clean run stop, same contract as the mutation account
        except Exception as e:
            logger.warning(f"PES planning call failed; iteration runs unplanned: {e}")
            return Advice()

        plan = (plan or "").strip()
        if not plan:
            return Advice()
        return Advice(
            prompt_block=plan,
            attribution={"plan": plan, "parent_id": ctx.parent.id},
        )

    def _build_planning_prompt(self, ctx: GenerationContext) -> str:
        parent = ctx.parent
        prior = self._plans.get(parent.id)
        if prior:
            prior_block = (
                f"Outcome of the plan that produced this solution: **{prior['outcome']}** "
                f"(fitness {prior['parent_fitness']:.4f} -> {prior['child_fitness']:.4f})\n\n"
                f"{prior['plan']}"
            )
        else:
            prior_block = "None — first plan for this lineage."

        code = parent.code
        if len(code) > self.max_code_chars:
            code = code[: self.max_code_chars] + "\n# ... (truncated)"

        return PLANNER_USER_TEMPLATE.format(
            fitness=parent.fitness,
            metrics={k: v for k, v in parent.metrics.items() if isinstance(v, (int, float))},
            code=code,
            prior_block=prior_block,
            best_history=[round(v, 4) for v in ctx.best_fitness_history[-_HISTORY_TAIL:]],
            avg_history=[round(v, 4) for v in ctx.avg_fitness_history[-_HISTORY_TAIL:]],
        )

    # ------------------------------------------------------- credit / state

    def report_result(
        self,
        ctx: GenerationContext,
        child: Optional[ProgramView],
        attribution: Dict[str, Any],
        eval_failed: bool,
    ) -> None:
        plan = attribution.get("plan")
        if not plan or ctx.parent is None:
            return
        if child is None:
            return  # no program produced: no lineage node to attach the plan to
        parent_fitness = ctx.parent.fitness
        child_fitness = child.fitness
        if eval_failed:
            outcome = FAILED
        elif child_fitness > parent_fitness:
            outcome = IMPROVED
        elif child_fitness < parent_fitness:
            outcome = REGRESSED
        else:
            outcome = STALE
        self._plans[child.id] = {
            "plan": plan,
            "outcome": outcome,
            "parent_fitness": parent_fitness,
            "child_fitness": child_fitness,
        }

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        return None  # Phase 2 (reflective summarizer) would live here

    def state_dict(self) -> Dict[str, Any]:
        return {"plans": self._plans}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self._plans = dict(state.get("plans", {}))

    def log_snapshot(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for entry in self._plans.values():
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
        return {"plans_stored": len(self._plans), "outcomes": counts}
