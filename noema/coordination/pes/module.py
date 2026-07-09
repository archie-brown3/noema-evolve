"""
- advise()          <- the Planner phase: one metered planning call per mutation,
                       plan injected as the standard coordination suffix
                       (LoongFlow: agents/general_agent/planner.py)
- report_result()   <- the Summary phase's _gather + _assess (pure Python):
                       computes IMPROVEMENT/REGRESSION/STALE vs parent score
                       (agents/general_agent/summary.py:228-247) and enqueues
                       the child for reflection. No LLM call here.
- on_generation_end <- the Summary phase's _reflect + _record (Phase 2): drains
                       the queue, one metered reflection call per child
                       (summary.py:249-409, Reflexion-derived), storing the
                       causal explanation so the next plan for that lineage
                       sees it (LoongFlow stores generate_plan/summary on every
                       solution: framework/pes/database/database.py:96-101)

Documented deviations from the released code (PLAN.md section 2.2 discipline):
1. One single-shot planning call, not a multi-turn Claude Code agent session.
   LoongFlow's own math_agent runs PES on plain LLM calls, so this recast has
   upstream precedent.
2. The plan is a prompt *suffix* after openevolve's mutation instructions, not
   the executor's primary directive. Biggest fidelity gap — flagged in the fit
   assessment; **closed by Stage 2** ([[tasks/0050-implement-stage2-reflection-seeded-retries]]):
   `retry_advice` now seeds retries with the lineage's causal reflection, making
   planning and execution iterative (plan → attempt → *why it failed* → retry
   informed by the reflection) rather than "plan once, append as static suffix."
3. The parent is sampled by the host and handed in via GenerationContext;
   LoongFlow's planner samples it itself and can query the database with
   tools. Lineage memory here is the module-internal plan/outcome store.
4. Reflection (Phase 2, "PES Phase 2 Plan" Stage 0) runs deferred at the
   generation tick rather than inline in report_result — report_result stays
   sync/no-LLM (its interface contract) and enqueues; on_generation_end (already
   async) makes the metered call. Round-robin island scheduling guarantees a
   lineage's own reflection lands before its next mutation, so this deferral is
   invisible to the one consumer that matters. Assessment stays a pure-Python
   fitness comparison (matches LoongFlow's _assess), only the causal explanation
   is an LLM call (matches _reflect).
5. "Expected Deliverables" is dropped from the plan skeleton: the deliverable
   is fixed by the substrate (a diff/rewrite of the program).
6. The "Recently Attempted Elsewhere" section of the planning prompt is
   noema-original (no LoongFlow precedent): a population-wide, cross-lineage
   digest of recent strategies + outcomes, to stop independent lineages
   converging on the same untested idea ("PES Phase 2 Plan" Design 2).
"""

import logging
from typing import Any, Dict, List, Optional

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

# =============================================================================
# BORROWED CODE — reflection prompt adapted from LoongFlow (Apache-2.0)
# Source: src/loongflow/framework/claude_code/general_prompt.py
#         (GENERAL_SUMMARY_SYSTEM lines 341-364, GENERAL_SUMMARY_USER lines
#         373-448; local clone /home/archie/LoongFlow)
# Condensed for a single-call recast (LoongFlow's _reflect is one agent.run
# call, summary.py:325); the "causal over correlational" instruction is kept
# verbatim-ish as the load-bearing line. Local changes marked NOEMA.
# =============================================================================

REFLECTION_SYSTEM = """You are a reflective analyst in a structured problem-solving system.
A plan was proposed and executed as a code mutation. Given the plan, the parent
solution it started from, the resulting child solution, and the measured outcome,
explain WHY the outcome happened.

Key principles:
- Causal over correlational: explain why the change worked or failed, not just
  that the score moved.
- Be concrete and brief: 2-4 sentences the next attempt can act on.
- On failure, name the specific cause (e.g. the reported error) and what to avoid."""
# NOEMA: condensed from GENERAL_SUMMARY_SYSTEM; the Assessment/What-Worked/
# What-Didn't/Insights/Recommendations section skeleton is dropped in favour of
# a short free-text explanation (single-call recast, prompt-suffix consumer).

REFLECTION_USER_TEMPLATE = """# Outcome to Explain
The plan below was executed as one mutation. Outcome: **{outcome}** \
(fitness {parent_fitness:.4f} -> {child_fitness:.4f}).{error_block}

# Plan That Was Executed
{plan}

# Parent Solution (fitness {parent_fitness:.4f})
```
{parent_code}
```

# Resulting Child Solution (fitness {child_fitness:.4f})
```
{child_code}
```

# Your Task
In 2-4 sentences, explain the CAUSE of this outcome and one concrete lesson for
the next mutation of this lineage. Output only the explanation."""

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
        # Problem-domain constraints (e.g. "explicit constructor, not iterative
        # search") — orthogonal to search mechanics, which the planner never
        # sees. Populated from NoemaConfig.prompt.system_message by the
        # controller unless the experiment overrides it in coordination.params.
        self.domain_context: str = self.config.get("domain_context", "")
        # Reflection (Phase 2 Stage 0). D1 escape hatches: disable entirely, or
        # cap how many queued children get reflected on per generation tick if
        # the coordination-account spend proves too high in practice.
        self.reflection_enabled: bool = self.config.get("reflection_enabled", True)
        self.max_pending_reflections_per_tick: Optional[int] = self.config.get(
            "max_pending_reflections_per_tick", None
        )
        # Cross-lineage diversity signal (Phase 2 Stage 0, Design 2). D4 knobs.
        self.recent_strategies_k: int = self.config.get("recent_strategies_k", 3)
        self.strategy_digest_chars: int = self.config.get("strategy_digest_chars", 150)
        # plan/outcome/reflection per child program id — the PES lineage memory
        self._plans: Dict[str, Dict[str, Any]] = {}
        # children awaiting a reflection call, drained at the generation tick
        self._pending_reflections: List[Dict[str, Any]] = []

    # ------------------------------------------------------------- advise

    async def advise(self, ctx: GenerationContext) -> Advice:
        if ctx.parent is None or self.llm is None:
            return Advice()

        prompt = self._build_planning_prompt(ctx)
        system_message = PLANNER_SYSTEM
        if self.domain_context:
            system_message = f"{PLANNER_SYSTEM}\n\n# Problem Domain\n{self.domain_context}"
        try:
            plan = await self.llm.generate_with_context(
                system_message=system_message,
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

    async def retry_advice(self, ctx: GenerationContext, error_text: str, attempt: int) -> str:
        """Seed a Stage-1 retry with this lineage's causal reflection (Design 4).

        Returns the stored reflection text (the "why it failed" from the deferred
        summary call) framed as a retry-guidance block, or "" when there's no
        parent or no reflection yet (fresh lineage). The controller concatenates
        this after its raw-error suffix; Null inherits the no-op, so only PES
        retries carry reflection — the controlled variable stays single.
        """
        if ctx.parent is None:
            return ""
        prior = self._plans.get(ctx.parent.id)
        reflection = prior.get("reflection") if prior else None
        if not reflection:
            return ""
        return (
            "\n# Reflection on the lineage's last failure\n"
            f"{reflection}\n"
            "Use this causal explanation to guide the corrected mutation."
        )

    def _truncate(self, code: str) -> str:
        if len(code) > self.max_code_chars:
            return code[: self.max_code_chars] + "\n# ... (truncated)"
        return code

    def _build_planning_prompt(self, ctx: GenerationContext) -> str:
        parent = ctx.parent
        prior = self._plans.get(parent.id)
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
            code=self._truncate(parent.code),
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
        and their outcomes — noema-original (deviation #6). Built from self._plans
        (flat across all islands/lineages, insertion-ordered = iteration-ordered),
        so a fresh lineage's first plan still sees what other islands already
        tried and failed. No LLM call: plain truncation of the `## Strategy`
        section (D4). Returns "" when there's nothing to show yet.
        """
        if self.recent_strategies_k <= 0:
            return ""
        seen = set()
        lines: List[str] = []
        for cid, entry in reversed(self._plans.items()):
            if cid == exclude_id:
                continue  # the lineage's own last plan is already in prior_block
            strategy = self._extract_strategy(entry.get("plan", ""))
            if not strategy:
                continue
            digest = strategy[: self.strategy_digest_chars].strip()
            key = digest.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{entry.get('outcome', '?')}] {digest}")
            if len(lines) >= self.recent_strategies_k:
                break
        if not lines:
            return ""
        return (
            "\n# Recently Attempted Elsewhere\n"
            "Strategies already tried across the population — avoid repeating the "
            "failed ones, and prefer a distinct approach:\n" + "\n".join(lines) + "\n"
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
        # Enqueue for the deferred reflection call (drained in on_generation_end).
        # Pure Python, no I/O here — report_result keeps its sync/no-LLM contract.
        # Snapshot everything the reflection prompt needs as primitives so the
        # queue stays JSON-serializable for checkpointing (D2). stderr comes from
        # child.metadata (the controller stamps the evaluator's error text there).
        if self.reflection_enabled and self.llm is not None:
            self._pending_reflections.append(
                {
                    "child_id": child.id,
                    "plan": plan,
                    "outcome": outcome,
                    "parent_fitness": parent_fitness,
                    "child_fitness": child_fitness,
                    "parent_code": self._truncate(ctx.parent.code),
                    "child_code": self._truncate(child.code),
                    "stderr": str(child.metadata.get("stderr", ""))[: self.max_code_chars],
                }
            )

    # ------------------------------------------------ reflection (Phase 2)

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        """
        Drain the reflection queue: one metered causal-reflection call per
        pending child (LoongFlow's Summary _reflect/_record, deferred here from
        report_result — see deviation #4). BudgetExhausted propagates (clean
        stop); other LLM failures degrade that entry to an empty reflection.
        """
        if not self.reflection_enabled or self.llm is None:
            self._pending_reflections.clear()
            return
        limit = self.max_pending_reflections_per_tick
        while self._pending_reflections:
            if limit is not None and limit <= 0:
                break
            entry = self._pending_reflections.pop(0)
            await self._reflect(entry)
            if limit is not None:
                limit -= 1

    async def _reflect(self, entry: Dict[str, Any]) -> None:
        child_id = entry["child_id"]
        if child_id not in self._plans:
            return  # lineage node gone (shouldn't happen; defensive)
        error_block = f"\n- Reported error: {entry['stderr']}" if entry.get("stderr") else ""
        prompt = REFLECTION_USER_TEMPLATE.format(
            outcome=entry["outcome"],
            parent_fitness=entry["parent_fitness"],
            child_fitness=entry["child_fitness"],
            error_block=error_block,
            plan=entry["plan"],
            parent_code=entry["parent_code"],
            child_code=entry["child_code"],
        )
        try:
            reflection = await self.llm.generate_with_context(
                system_message=REFLECTION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tag="pes.reflect",
            )
        except BudgetExhausted:
            raise  # clean run stop, same contract as the planning call
        except Exception as e:
            logger.warning(f"PES reflection call failed; lineage keeps plain outcome: {e}")
            self._plans[child_id]["reflection"] = ""
            return
        self._plans[child_id]["reflection"] = (reflection or "").strip()

    # ----------------------------------------------------------- persistence

    def state_dict(self) -> Dict[str, Any]:
        # D2: persist the pending queue so a checkpoint resume doesn't silently
        # drop children that were enqueued but not yet reflected on.
        return {"plans": self._plans, "pending_reflections": self._pending_reflections}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self._plans = dict(state.get("plans", {}))
        self._pending_reflections = list(state.get("pending_reflections", []))

    def log_snapshot(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        reflections = 0
        for entry in self._plans.values():
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
            if entry.get("reflection"):
                reflections += 1
        return {
            "plans_stored": len(self._plans),
            "outcomes": counts,
            "reflections_stored": reflections,
            "pending_reflections": len(self._pending_reflections),
        }
