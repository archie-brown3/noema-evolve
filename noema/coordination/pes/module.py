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
from noema.coordination.pes.executor import Executor
from noema.coordination.pes.planner import (  # noqa: F401  (re-exported)
    _HISTORY_TAIL,
    PLANNER_SYSTEM,
    PLANNER_USER_TEMPLATE,
    Planner,
)
from noema.substrate.views import ProgramView

logger = logging.getLogger(__name__)

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
        # Phase objects (task 0060): share this module's state by reference.
        self._planner = Planner(self)
        self._executor = Executor(self)

    # ------------------------------------------------------------- advise

    async def advise(self, ctx: GenerationContext) -> Advice:
        if ctx.parent is None or self.llm is None:
            return Advice()
        plan = await self._planner.plan(ctx)
        if not plan:
            return Advice()
        return self._executor.build_advice(plan, ctx)

    async def retry_advice(self, ctx: GenerationContext, error_text: str, attempt: int) -> str:
        """Reflection-seeded retries (Design 4) — see Executor.retry_block."""
        return self._executor.retry_block(ctx, error_text, attempt)

    def _truncate(self, code: str) -> str:
        if len(code) > self.max_code_chars:
            return code[: self.max_code_chars] + "\n# ... (truncated)"
        return code

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
