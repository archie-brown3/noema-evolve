"""
Summary phase of the PES arm (LoongFlow: agents/general_agent/summary.py).

Extracted from module.py (task 0060, behavior-identical split). Assessment is
pure Python (LoongFlow's _assess); only the causal reflection is an LLM call
(LoongFlow's _reflect), drained deferred at the generation tick — see the
module docstring's deviation #4.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict

from noema.budget.ledger import BudgetExhausted
from noema.coordination.base import GenerationContext
from noema.coordination.pes.planner import Planner
from noema.views import ProgramView

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

# =============================================================================
# BORROWED CODE — pes-faithful summary prompt, near-verbatim from LoongFlow
# (Apache-2.0). Source: https://github.com/baidu-baige/LoongFlow
#   agents/math_agent/prompt/evolve_summary_prompt.py
#   (EVOLVE_SUMMARY_SYSTEM_PROMPT lines 6-84, EVOLVE_SUMMARY_USER_PROMPT
#   lines 86-151). Single-call shape per summary/summary_agent_finalizer.py.
#   Change ledger: [[PES Faithful Prompt Recast Design — 2026-07-10]] §2
#   (vault) — KEEP lines are verbatim (incl. the "Do not fail in this duty"
#   pressure line: it is the treatment); every ADAPT is marked # NOEMA below.
#   Trailing whitespace normalized.
# =============================================================================

FAITHFUL_REFLECTION_SYSTEM = """We are currently using an Algorithm Evolve Paradigm (Evolux) to solve an evolve task. In Evolux, there are three phases:

*   **Phase 1: Planner.** Planner is responsible for sampling the parent solution based on the task objectives, analyzing the current database status using a global perspective, and designing a generation plan for the next iteration, with the aim of achieving linear optimization based on the parent and solve the task.
*   **Phase 2: Executor.** Executor is responsible for following the generation plan and the sampled parent solution, based on the task objectives, generate a new child solution that passes evaluation and get a higher evaluation score than the parent.
*   **Phase 3: Summary.** Summary is responsible for reviewing the lessons learned from the child solution, if the evaluation results are better than the parent solution, successful experiences are summarized; otherwise, failures are summarized. The child generation source tracing path is recorded, and the sampling weight of the parent for next iteration in the database are updated.

This achieves a self-evolutionary closed loop across Phases 1, 2 and 3.

---

# ROLE & MISSION

Now, you are **Phase 3: Summary**, the strategic brain of the Evolux framework.
You are a **Strategic Analyst** and **Evolution Navigator** for the Evolux framework.
Your role is not to merely report on the past, but to generate the wisdom that fuels future evolution.

Your analysis must be objective, data-driven, and relentlessly focused on one question: **"What is the most effective way to accelerate our evolution towards the goal?"**

Your **sole mission** is to produce a final, analysis report:
1.  **A qualitative `comparative_analysis`**: A professional strategic brief.

---

# STANDARD OPERATING PROCEDURE

Follow this rigorous, two-step process precisely.

### **STEP 1: Data Collection & Contextualization**

*   **Priority 1: Population Analysis.** Get the full picture of the "family." The `# 4. Sibling Solutions` section of your prompt lists all sibling solutions and their scores. This is not optional and is your primary context.
*   **Priority 2: Parent-Child Comparison.** Conduct the standard analysis of the direct lineage.
*   **Priority 3: Ancestry Trace (if needed).** Use the lineage information provided in your prompt (the parent solution's `generate_plan` and `summary` fields) to understand the origin of key ideas if the context is unclear.

### **STEP 2: Final Report Generation**

After completing your data collection, generate the final report as follows:

#### **Generate Strategic Brief (`comparative_analysis`)**
*   Your analysis MUST be a professional, multi-dimensional strategic brief. Follow this structure precisely.

**1. Executive Summary:**
*   **Format:** A single, powerful sentence.
*   **Mandatory Content:** You MUST answer "What was the outcome and why?" using this template:
    `This iteration was a [Nature of Outcome] because [Core Causal Factor], performing [Relative Performance vs. Siblings].`
*   **Example:** `This iteration was a pivotal breakthrough because its novel 'Voronoi partitioning' strategy doubled the score, dramatically outperforming all siblings.`
*   **AVOID:** Vague, low-information sentences like "This was a successful iteration."

**2. Data-Driven Findings (Facts ONLY):**
*   **Format:** A bulleted list of objective metrics. NO interpretations.
*   **Mandatory Checklist:**
    *   `**Sibling Rank:**` X out of Y children. Top sibling score: Z%.
    *   `**Score Delta:**` From parent (A%) to child (B%).
    *   `**Key Change:**` [The single most significant algorithmic or structural modification].
    *   `**Core Metrics:**` [e.g., Runtime, Memory Usage, specific problem constraints].

**3. Strategic Analysis (The "So What?"):**
*   **Format:** Your subjective interpretation connecting the dots from the data above.
*   **Mandatory Questions to Answer:**
    *   `**Root Cause:**` Was the outcome due to the *quality of the plan* itself, or the *quality of its execution*? Or both?
    *   `**Key Insight:**` What is the single most important lesson learned from this iteration? Is there a valuable concept to salvage even from a failure?
    *   `**Identified Risk:**` What is the biggest weakness or potential trap revealed by this solution (e.g., over-complexity, a potential local optimum)?

**4. Actionable Guidance (The "What's Next?"):**
*   **Core Principle: Be a creative strategist, not just a bug fixer.** Your advice should open up new evolutionary paths.
*   **Format:** Use the guiding tags (`Recommend Fusion`, `Recommend Stripping`, `Recommend Exploration`, `Warn`).
*   **AVOID:** Simply restating the risks from Part 3 as recommendations (e.g., "Fix the over-complexity").
*   **Example of GOOD (Strategic) vs. BAD (Shallow) advice:**
    *   **BAD:** `Recommend Exploration: Try to improve the score.`
    *   **GOOD:** `Recommend Fusion: The 'local search' module from this solution is highly effective. Recommend fusing it into the top-performing sibling's 'Delaunay' framework to combine global exploration with local optimization.`
---

# FINAL DIRECTIVE

Once you have generated the complete report, you **MUST** output it directly as your response, and output nothing else. This is the final step of your mission.

**VERY IMPORTANT**: Your analysis is the navigation system for this entire evolutionary journey. A shallow or context-free report will cause the system to wander aimlessly, wasting immense resources. Your deep, population-aware insights are what will guide it to a breakthrough. Do not fail in this duty.
**VERY IMPORTANT**: You should do this task by yourself, don't need to ask help or confirmation from the user or others !!!"""
# NOEMA (§2.1) ADAPTs vs EVOLVE_SUMMARY_SYSTEM_PROMPT, in order: STEP 1
# Priority 1's tool fetch ("Use your tools (e.g., `get_childs_by_parent_id`) to
# fetch all sibling solutions and their scores") -> the pre-injected
# `# 4. Sibling Solutions` section; Priority 3's `get_parents_by_child_id`
# -> the lineage fields already in the prompt; FINAL DIRECTIVE's
# `generate_final_answer` tool call -> "output it directly as your response".
# Everything else — ROLE & MISSION, the whole four-section brief spec, the
# GOOD/BAD example, both VERY IMPORTANT closers — is verbatim.

FAITHFUL_REFLECTION_USER_TEMPLATE = """You are the **Summary** phase of Evolux, the strategic brain of the evolutionary framework.

Your task is to analyze the data for the current iteration and produce a comprehensive summary report.
This report is critical for guiding the future direction of our evolution.
Follow the directives and workflow outlined in your system instructions.

# 1. Data Field Glossary

This glossary defines the fields for a Solution entity within the Evolux framework. Understanding these roles is key to your analysis.

- `solution_id`: A unique identifier for a solution **once it is saved to the database**. Used for tracking and lineage tracing.
- `parent_id`: The `solution_id` of the direct ancestor, establishing the evolutionary lineage.
- `generate_plan`: The strategic blueprint from the **Planner** that guides the **Executor** in creating a new solution.
- `solution`: The complete, executable source code representing the "genetic material" of an evolutionary step.
- `score`: A quantitative measure of a solution's fitness (completion ratio). A score of `1.0` or greater means the task objective is met.
- `evaluation`: The raw output and logs from the fitness evaluation process, providing evidence for the `score`.
- `summary`: **The strategic analysis that you are responsible for generating.** It provides qualitative insights for future Planners.
- other fields: You can ignore other fields as they are irrelevant to the analysis.

# 2. Global Task Information
{task_info}

## Time Limit
If the task information shows there is a time limit, that means the generated solution needs to return within the time requirement.
HOWEVER, You CAN NOT assume that the shorter the solution execution time, the better the solution evaluation performance.
Our goal is to complete the task. As long as the solution doesn't exceed the time limit during execution, it's a good solution. When you find that performance evaluation is not improving, don't get bogged down in optimizing execution time.

# 3. Iteration Data for Analysis

Below are the data dictionaries for the parent and the new child solution.
Pay close attention to the notes which explain their state in the lifecycle.

## Parent Solution (The Baseline)

This is the complete, archived solution from which the new solution was evolved.

Note: If 'solution_id' is 'None' or empty, this is a GENESIS solution.
As the starting point of an evolutionary line, its other fields will be empty or have default values.

```json
{parent_solution}
```

## Current Solution (Pending Your Analysis)

This is the new solution you must analyze. It is temporary and not yet archived.

Note: This solution is pending your analysis and has not been saved.
Consequently, any values for 'solution_id' or 'summary' are placeholders and must be disregarded.
Your primary task is to generate the definitive 'summary' for this solution.

```json
{current_solution}
```

## Assessment Result

Assessment is a qualitative, system-provided assessment comparing the current solution to its parent.

```
{assessment_result}
```

# 4. Sibling Solutions

These are all the children of the same parent — the "family" your Population Analysis (STEP 1, Priority 1) requires. The statistics below are computed for you; copy them into your Data-Driven Findings checklist rather than recalculating them.

{sibling_block}

Begin your analysis now. Output the comparative_analysis report directly as your response, and output nothing else."""
# NOEMA (§2.2) ADAPTs vs EVOLVE_SUMMARY_USER_PROMPT, in order: the Assessment
# Result preamble's "human-provided" -> "system-provided" (one word: the label
# comes from the pure-Python classifier, not a person); the whole
# `# 4. Sibling Solutions` section is NEW (the recast of STEP 1's tool fetch —
# host-precomputed X/Y/Z stat lines + the sibling table, so the 14B model
# copies rather than computes); the closing line is inverted (output directly,
# don't call generate_final_answer). Everything else — the glossary, Global
# Task Information + Time Limit, the Iteration Data preamble, both solution
# blocks with their GENESIS/placeholder notes — is verbatim.
# {task_info} <- domain_context; {parent_solution}/{current_solution} <-
# lineage JSON dicts; {assessment_result} <- classifier label + score delta.

# ============================== END BORROWED =================================

# Section headers of the four-part brief. The capped downstream slice carries
# only Executive Summary + Actionable Guidance (design note §2.3(a)).
BRIEF_EXEC_SUMMARY_HEADER = "**1. Executive Summary:**"
BRIEF_GUIDANCE_HEADER = "**4. Actionable Guidance"
# Floor for the faithful reflection completion (design note §2.3).
FAITHFUL_REFLECTION_MIN_TOKENS = 1024
# Same rough estimator the ledger uses for servers that omit usage counts —
# good enough for a pre-flight size guard (it never bills anything).
_CHARS_PER_TOKEN = 4

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
            "parent_id": ctx.parent.id,
            "parent_fitness": parent_fitness,
            "child_fitness": child_fitness,
        }
        # Pure Python, no I/O here — report_result keeps its sync/no-LLM contract.
        # Snapshot everything the reflection prompt needs as primitives so the
        # queue stays JSON-serializable for checkpointing (D2). stderr comes from
        # child.metadata (the controller stamps the evaluator's error text there).
        # An empty plan means the planning call failed (task 0042): the lineage
        # entry above still stands, but there is no plan to reflect on.
        if plan and m.reflection_enabled and m.llm is not None:
            m._pending_reflections.append(
                {
                    "child_id": child.id,
                    "parent_id": ctx.parent.id,
                    "plan": plan,
                    "outcome": outcome,
                    "parent_fitness": parent_fitness,
                    "child_fitness": child_fitness,
                    "parent_code": m._truncate(ctx.parent.code),
                    "child_code": m._truncate(child.code),
                    "stderr": str(child.metadata.get("stderr", ""))[: m.max_code_chars],
                    # numeric metrics only — the faithful brief's `evaluation`
                    # field pairs them with stderr as the score's evidence
                    "metrics": {
                        k: v
                        for k, v in child.metrics.items()
                        if isinstance(v, (int, float))
                    },
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
        faithful = m.prompt_variant == "faithful"
        if faithful:
            system_message = FAITHFUL_REFLECTION_SYSTEM
            prompt = self._build_faithful_prompt(entry)
            call_kwargs = {"max_tokens": self._faithful_max_tokens()}
            # Pre-flight, never mid-run (design note §2.3(b)): the substrate's
            # context is locked and was already patched once for overflow, so a
            # prompt that would overflow it fails loudly here rather than being
            # silently truncated by the server — silent truncation would change
            # the treatment invisibly.
            self._assert_prompt_fits(system_message, prompt, call_kwargs["max_tokens"])
        else:
            error_block = (
                f"\n- Reported error: {entry['stderr']}" if entry.get("stderr") else ""
            )
            system_message = REFLECTION_SYSTEM
            prompt = REFLECTION_USER_TEMPLATE.format(
                outcome=entry["outcome"],
                parent_fitness=entry["parent_fitness"],
                child_fitness=entry["child_fitness"],
                error_block=error_block,
                plan=entry["plan"],
                parent_code=entry["parent_code"],
                child_code=entry["child_code"],
            )
            call_kwargs = {}
        try:
            reflection = await m.llm.generate_with_context(
                system_message=system_message,
                messages=[{"role": "user", "content": prompt}],
                tag="pes.reflect",
                **call_kwargs,
            )
        except BudgetExhausted:
            raise  # clean run stop, same contract as the planning call
        except Exception as e:
            logger.warning(f"PES reflection call failed; lineage keeps plain outcome: {e}")
            m._plans[child_id]["reflection"] = ""
            return
        brief = (reflection or "").strip()
        if not faithful:
            m._plans[child_id]["reflection"] = brief
            return
        # Storage split (design note §2.3(a)): the whole brief stays in module
        # state; ONLY the capped Executive Summary + Actionable Guidance slice
        # re-enters downstream prompts.
        m._plans[child_id]["reflection_full"] = brief
        m._plans[child_id]["reflection"] = self._downstream_slice(brief)

    # ------------------------------------------------ faithful variant (0064)

    def _build_faithful_prompt(self, entry: Dict[str, Any]) -> str:
        m = self._m
        parent_id = entry.get("parent_id")
        parent_entry = m._plans.get(parent_id) if parent_id else None
        # GENESIS parent (no stored lineage entry): solution_id None + empty
        # fields, exactly the state the prompt's own note describes.
        parent_solution = {
            "solution_id": parent_id if parent_entry else None,
            "parent_id": parent_entry.get("parent_id") if parent_entry else None,
            "generate_plan": parent_entry.get("plan") if parent_entry else None,
            "solution": entry["parent_code"],
            "score": entry["parent_fitness"],
            "evaluation": None,
            "summary": parent_entry.get("reflection") if parent_entry else None,
        }
        current_solution = {
            "solution_id": entry["child_id"],
            "parent_id": parent_id,
            "generate_plan": entry["plan"],
            "solution": entry["child_code"],
            "score": entry["child_fitness"],
            "evaluation": {
                "stderr": entry.get("stderr", ""),
                "metrics": entry.get("metrics", {}),
            },
            "summary": None,  # the placeholder the prompt tells it to disregard
        }
        delta = entry["child_fitness"] - entry["parent_fitness"]
        assessment = (
            f"{entry['outcome'].upper()}: fitness {entry['parent_fitness']:.4f} -> "
            f"{entry['child_fitness']:.4f} (score delta {delta:+.4f})"
        )
        return FAITHFUL_REFLECTION_USER_TEMPLATE.format(
            task_info=m.domain_context or "None provided.",
            parent_solution=json.dumps(parent_solution, indent=2),
            current_solution=json.dumps(current_solution, indent=2),
            assessment_result=assessment,
            sibling_block=self._sibling_block(entry),
        )

    def _sibling_block(self, entry: Dict[str, Any]) -> str:
        """Host-precomputed `# 4. Sibling Solutions` content (the recast of
        LoongFlow's `get_childs_by_parent_id` tool fetch).

        X (rank), Y (count) and Z (top score) are computed here so the model
        copies them into its checklist instead of computing them — the key 14B
        mitigation (design note §2.2). Siblings come from `_plans` filtered by
        `parent_id` (stored since task 0060); the current child is already
        recorded, so it is in the family it is ranked against.
        """
        m = self._m
        child_id = entry["child_id"]
        parent_id = entry.get("parent_id")
        family = [
            (cid, e)
            for cid, e in m._plans.items()
            if parent_id is not None and e.get("parent_id") == parent_id
        ]
        if not family:
            # No identifiable family: a GENESIS/blank parent id, or a queue
            # entry checkpointed before parent_id existed. Degenerate to the
            # only-child case rather than rendering "0 out of 0" stats the
            # model is told to copy verbatim (0064 verifier finding 1).
            own = m._plans.get(child_id)
            family = [(child_id, own)] if own is not None else []
        # Deterministic: score-descending, insertion order (= iteration order)
        # breaking ties, so the same state always renders the same block.
        ranked = sorted(family, key=lambda kv: -kv[1].get("child_fitness", 0.0))
        total = len(ranked)
        rank = next(
            (i + 1 for i, (cid, _) in enumerate(ranked) if cid == child_id), total
        )
        top = ranked[0][1].get("child_fitness", 0.0) if ranked else 0.0
        lines = [
            f"Total children of this parent (Y): {total}",
            f"Current solution's rank by score (X): {rank} out of {total}",
            f"Top sibling score (Z): {top:.4f}",
        ]
        if total <= 1:
            lines.append(
                "This solution is an only child: its rank is 1 out of 1 and there are "
                "no siblings to compare against. State this plainly."
            )

        # Bound the TABLE (task 0067). X/Y/Z above are already computed over the
        # whole family and stay true; only the rendered rows are limited, so a
        # parent with many children cannot grow this prompt without bound. The
        # current solution is always shown — it is the one being reflected on.
        cap = m.max_siblings_rendered
        shown = ranked
        if cap is not None and total > cap:
            if cap <= 0:
                own = next((kv for kv in ranked if kv[0] == child_id), None)
                shown = [own] if own is not None else []
            else:
                shown = ranked[:cap]
                if not any(cid == child_id for cid, _ in shown):
                    own = next((kv for kv in ranked if kv[0] == child_id), None)
                    if own is not None:
                        shown = ranked[: cap - 1] + [own]
            lines.append(
                f"Showing the top {len(shown)} of {total} children by score; the "
                "current solution is always included. Y and X above are the TRUE "
                "family totals — use them, not the row count."
            )

        lines.append("")
        lines.append("| solution_id | score | outcome | strategy |")
        lines.append("| --- | --- | --- | --- |")
        for cid, e in shown:
            marker = " (current)" if cid == child_id else ""
            lines.append(
                f"| {cid}{marker} | {e.get('child_fitness', 0.0):.4f} | "
                f"{e.get('outcome', '?')} | {self._strategy_digest(e.get('plan', ''))} |"
            )
        return "\n".join(lines)

    def _strategy_digest(self, plan_text: str) -> str:
        """One-line digest of a sibling's plan for the table cell. Prefers the
        custom plan's `## Strategy` section (already newline-flattened by
        _extract_strategy); the faithful plan has no such section, so it falls
        back to the plan's opening text. Pipes are escaped — an unescaped `|`
        in a plan would add phantom columns to the table the model reads its
        stats from (0064 verifier finding 2). No LLM call."""
        strategy = Planner._extract_strategy(plan_text)
        if not strategy:
            strategy = " ".join(plan_text.split())
        strategy = strategy.replace("|", "\\|")
        return strategy[: self._m.strategy_digest_chars].strip() or "-"

    def _downstream_slice(self, brief: str) -> str:
        """The ONLY part of the brief that re-enters later prompts: Executive
        Summary + Actionable Guidance, token-capped (design note §2.3(a),(c)).

        Also strips any preamble the model emits before the first `**1.`
        section header. Falls back to the capped whole brief when the model
        ignored the section structure (the shakedown's format-compliance gate
        counts those, it is not this method's job to hide them).
        """
        m = self._m
        body = brief
        start = body.find("**1.")
        if start > 0:
            body = body[start:]
        parts = []
        exec_start = body.find(BRIEF_EXEC_SUMMARY_HEADER)
        if exec_start >= 0:
            exec_end = body.find("**2.", exec_start)
            parts.append(
                body[exec_start : exec_end if exec_end >= 0 else len(body)].strip()
            )
        guidance_start = body.find(BRIEF_GUIDANCE_HEADER)
        if guidance_start >= 0:
            parts.append(body[guidance_start:].strip())
        text = "\n\n".join(parts) if parts else body.strip()
        cap = m.reflection_slice_max_tokens * _CHARS_PER_TOKEN
        if len(text) > cap:
            text = text[:cap].rstrip() + "\n... (truncated)"
        return text

    def _faithful_max_tokens(self) -> int:
        """Completion cap for the faithful reflect call: at least
        FAITHFUL_REFLECTION_MIN_TOKENS (design note §2.3); a configured cap
        above the floor is kept. Sent explicitly even when unset, so a local
        server's low default cannot truncate the brief mid-section."""
        configured = getattr(self._m.llm, "max_tokens", None)
        return max(configured or 0, FAITHFUL_REFLECTION_MIN_TOKENS)

    def _assert_prompt_fits(self, system_message: str, prompt: str, max_tokens: int) -> None:
        """Fail loud before dispatch if prompt + reserved completion would
        overflow the substrate's locked context window (design note §2.3(b))."""
        m = self._m
        estimated = (len(system_message) + len(prompt)) // _CHARS_PER_TOKEN
        if estimated + max_tokens > m.context_window_tokens:
            raise ValueError(
                "PES faithful reflection prompt would overflow the context window: "
                f"~{estimated} prompt tokens + {max_tokens} reserved completion tokens "
                f"> {m.context_window_tokens} (context_window_tokens). Reduce "
                "max_code_chars or raise the window; never let it truncate silently."
            )
