"""
Summary phase of the PES arm (LoongFlow: agents/general_agent/summary.py).

Extracted from module.py (task 0060, behavior-identical split). Assessment is
pure Python (LoongFlow's _assess); only the causal reflection is an LLM call
(LoongFlow's _reflect), drained deferred at the generation tick — see the
module docstring's deviation #4.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict

from noema.budget.ledger import BudgetExhausted
from noema.coordination.base import GenerationContext
from noema.substrate.views import ProgramView

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from noema.coordination.pes.module import PESPlannerModule

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


class Summarizer:
    """Summary phase: pure-Python assessment, lineage recording, and the
    deferred reflection queue. Shared state lives on the module façade."""

    def __init__(self, module: "PESPlannerModule"):
        self._m = module

    @staticmethod
    def assess(parent_fitness: float, child_fitness: float, eval_failed: bool) -> str:
        """Classify one mutation outcome (LoongFlow's _assess, pure Python)."""
        if eval_failed:
            return FAILED
        if child_fitness > parent_fitness:
            return IMPROVED
        if child_fitness < parent_fitness:
            return REGRESSED
        return STALE

    def record(
        self,
        ctx: GenerationContext,
        child: ProgramView,
        plan: str,
        eval_failed: bool,
    ) -> None:
        """Assess the outcome, store the lineage entry, and enqueue the child
        for the deferred reflection call (drained in on_generation_end)."""
        m = self._m
        parent_fitness = ctx.parent.fitness
        child_fitness = child.fitness
        outcome = self.assess(parent_fitness, child_fitness, eval_failed)
        m._plans[child.id] = {
            "plan": plan,
            "outcome": outcome,
            "parent_fitness": parent_fitness,
            "child_fitness": child_fitness,
        }
        # Pure Python, no I/O here — report_result keeps its sync/no-LLM contract.
        # Snapshot everything the reflection prompt needs as primitives so the
        # queue stays JSON-serializable for checkpointing (D2). stderr comes from
        # child.metadata (the controller stamps the evaluator's error text there).
        if m.reflection_enabled and m.llm is not None:
            m._pending_reflections.append(
                {
                    "child_id": child.id,
                    "plan": plan,
                    "outcome": outcome,
                    "parent_fitness": parent_fitness,
                    "child_fitness": child_fitness,
                    "parent_code": m._truncate(ctx.parent.code),
                    "child_code": m._truncate(child.code),
                    "stderr": str(child.metadata.get("stderr", ""))[: m.max_code_chars],
                }
            )

    # ------------------------------------------------ reflection (Phase 2)

    async def reflect_pending(self) -> None:
        """
        Drain the reflection queue: one metered causal-reflection call per
        pending child (LoongFlow's Summary _reflect/_record, deferred here from
        report_result — see the module docstring's deviation #4). BudgetExhausted
        propagates (clean stop); other LLM failures degrade that entry to an
        empty reflection.
        """
        m = self._m
        if not m.reflection_enabled or m.llm is None:
            m._pending_reflections.clear()
            return
        limit = m.max_pending_reflections_per_tick
        while m._pending_reflections:
            if limit is not None and limit <= 0:
                break
            entry = m._pending_reflections.pop(0)
            await self._reflect(entry)
            if limit is not None:
                limit -= 1

    async def _reflect(self, entry: Dict[str, Any]) -> None:
        m = self._m
        child_id = entry["child_id"]
        if child_id not in m._plans:
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
            reflection = await m.llm.generate_with_context(
                system_message=REFLECTION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tag="pes.reflect",
            )
        except BudgetExhausted:
            raise  # clean run stop, same contract as the planning call
        except Exception as e:
            logger.warning(f"PES reflection call failed; lineage keeps plain outcome: {e}")
            m._plans[child_id]["reflection"] = ""
            return
        m._plans[child_id]["reflection"] = (reflection or "").strip()
