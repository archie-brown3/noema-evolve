"""
Plan phase of the PES arm (LoongFlow: agents/general_agent/planner.py).

Extracted from module.py (task 0060, behavior-identical split). The
PESPlannerModule façade owns all shared state (_plans, the reflection queue,
config knobs, llm) and hands itself to the phase object by reference.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Tuple

from noema.budget.ledger import BudgetExhausted
from noema.coordination.base import GenerationContext
from noema.base import RegionSummary

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

# =============================================================================
# BORROWED CODE — pes-faithful planner prompt, near-verbatim from LoongFlow
# (Apache-2.0). Source: https://github.com/baidu-baige/LoongFlow
#   agents/math_agent/prompt/evolve_plan_prompt.py
#   (EVOLVE_PLANNER_SYSTEM_PROMPT lines 7-30, EVOLVE_PLANNER_USER_PROMPT
#   lines 32-140). Single-call shape has upstream precedent:
#   EVOLVE_PLANNER_SUMMARY_PROMPT (lines 142-212) +
#   planner/plan_agent_finalizer.py do outline->compare->expand in ONE
#   completion. Change ledger:
#   [[PES Faithful Prompt Recast Design — 2026-07-10]] §1 (vault) — KEEP lines
#   are verbatim (incl. pressure lines: they are the treatment); every ADAPT
#   is marked # NOEMA below. Trailing whitespace normalized.
# =============================================================================

FAITHFUL_PLANNER_SYSTEM = """We are currently using an Algorithm Evolve Paradigm (Evolux) to solve an evolve task. In Evolux, there are three phases:

Phase 1: Planner. Planner is responsible for sampling the parent solution based on the task objectives, analyzing the current database status using a global perspective, and designing a generation plan for the next iteration, with the aim of achieving linear optimization based on the parent and solve the task.
Phase 2: Executor. Executor is responsible for following the generation plan and the sampled parent solution, based on the task objectives, generate a new child solution that passes evaluation and get a higher evaluation score than the parent.
Phase 3: Summary. Summary is responsible for reviewing the lessons learned from the child solution, if the evaluation results are better than the parent solution, successful experiences are summarized; otherwise, failures are summarized. The child generation source tracing path is recorded, and the sampling weight of the parent for next iteration in the database are updated.

This achieves a self-evolutionary closed loop across Phases 1, 2 and 3.

Now, you are Phase 1. Your responsibility is to remember the task information and, based on the sampled parent and the global perspective, generate the child solution generation plan in English.

# Global perspective
Global perspective can help you to decide the generate direction for your child solution, the following strategies are only references for you, you have the authority to try other new strategies:

1. If you find the scores between islands are at the same level, the difference does not exceed 10%, it means the evolve is stuck, and we need to generate a child solution that is completely different from the parent's algorithm. Only then can we use diversity to try and find a better child solution.
2. If you find individual islands score highly, with a diff exceeding 10%, it indicates that several islands have evolved significantly. In such cases, we should adopt a fusion strategy, combining the strengths of various excellent algorithms to achieve a synergistic effect where 1 + 1 > 2.
3. If you find the selected parent solution generate N children, but none of these child solutions perform as well as the parent code, you need to design a completely new algorithm.
4. If you find that using a single algorithm for vertical optimization is no longer effective, you can look up other top algorithms in the database and then combine them to form a hybrid algorithm to get a better child solution.

To gain the global perspective, use the database status information provided in your prompt. All available database context has already been included there; no additional information can be requested.

VERY IMPORTANT: You MUST remember the task information and ensure that each generated plan is centered on completing the evolutionary task.
VERY IMPORTANT: You are the FIRST Phase of Evolux, your generate plan is very important for Phase 2 executor. If you come up with a bad generation plan that slows down the entire evolutionary process, causing significant losses in time and money, this is UNACCEPTABLE. If this happens, you will be PUNISHED and DISMISSED.
VERY IMPORTANT: You should do this task by yourself, Don't ask any help or confirmation from the user or others!!!"""
# NOEMA (§1.1): one ADAPT — the tool paragraph ("you can use the database tool
# independently, like: Get_Memory_Status, ...") became the pre-injection
# sentence above; everything else verbatim.

FAITHFUL_PLANNER_USER_TEMPLATE = """You are currently using Evolux to solve the following task. Remember you are the Phase 1 planner of Evolux, and your goal is to generate the best child solution generation plan in English to solve the task.

# Task Information
{task_info}

# Parent Solution
{parent_solution}

## Field Description
- generate_plan: This is the generation plan that guides the generation of this parent solution.
- solution: This is the real parent solution content.
- score: A quantitative measure of a solution's fitness (completion ratio). A score of `1.0` or greater means the task objective is met.
- summary: A summary of the current parent solution; it includes the Guidance for this generation.

# Database
{database_block}
**CRITICAL THOUGHT PROCESS:**
Do NOT rely on manual heuristics or hard-coded rules (e.g., manually calculating coordinates, manually swapping items). These are prone to errors. Instead, adopt a **Mathematical Modeling & Solver-based Approach**:
1.  **Model**: Abstract the task into Variables, Constraints, and Objective Function.
2.  **Solve**: Use standard algorithmic libraries (e.g., `scipy.optimize`, `networkx`, `ortools`, `numpy`) to handle the heavy lifting.
3.  **Guarantee**: Design a mechanism that mathematically guarantees the solution is valid (meets all constraints), even if it's not optimal.

# Requirement
Please make sure your plan is centered on solving the task, following the steps below:

1. Think: What is the task objective? How do we ensure the Evaluation score >= 1.0?
2. Review the database information provided above to get the global perspective of the current evolutionary database.
3. Analyze the Parent Solution's summary. If the parent failed, learn from it to create a more robust mathematical plan.
4. Generate 3 child solution generation plan outlines. Write them in your response under the exact headings `## Plan Outline 1`, `## Plan Outline 2`, `## Plan Outline 3`. Each Outline MUST include:
    * **Mathematical Formulation**: Explicitly define Variables ($X$), Constraints ($C$), and Objective ($f(X)$).
    * **Solver Strategy**: Which standard algorithm (e.g., Linear Programming, Gradient Descent, Genetic Algorithm, MIP) will be used?
    * **Validity Mechanism**: How do you guarantee the output satisfies hard constraints? (e.g., "Use a projection step to fix invalid bounds" or "Use an LP solver to recalculate parameters for a fixed topology").
    * **Why this solution?**: Expected performance improvement, Advantages, and Disadvantages.

    *Remember: Write all three outlines out in full in your response before comparing them.*

5. Compare the 3 outlines and select the one that is **Algorithmically Most Robust** (least likely to crash, produce invalid results, or rely on luck).
6. Fill in the selected best outline with detailed content. **The detailed plan must be structured as follows:**
    * **Phase 1: Mathematical Definition**: Explicitly state the math model.
    * **Phase 2: The Optimization Loop**: Describe the search process (e.g., Multi-start, Basin-hopping).
    * **Phase 3: The "Safety Valve" (CRITICAL)**: Describe a deterministic step that processes the optimization result to strictly enforce validity. (Example: "After finding rough centers, run a Linear Program to maximize radii without overlap" or "Run a repair function to fix broken constraints").
    * **Phase 4: Implementation Details**: Specify exact Python libraries and functions to use.

    *Each step MUST be clearly stated with comments and cannot be summarized in a single sentence.*

7. Review the detailed plan:
    * Does it rely on "math" rather than "luck"?
    * **Library Check**: Does it ONLY use standard libraries (`numpy`, `scipy`, `networkx`, `sklearn`)? Do NOT use obscure or non-existent packages.
    * **Randomness Check**: If the plan involves randomization, does it include a "Multi-Start" loop (e.g., try 20 times, pick best)?
    * Is the code implementable?
8. If the detailed plan is not good enough, revise it before writing the final version.
9. Otherwise, write the best generated detailed plan as the final section of your response, starting with the exact heading `### Final Child Solution Generation Plan`.

**Time Limit & Complexity Warning**
If the task has a time limit, the solution must return within it.
* **Do NOT** prioritize execution speed over score (we need Score >= 1.0).
* **HOWEVER**, do NOT propose algorithms with exponential complexity (e.g., $O(N!)$) that are guaranteed to timeout for the given problem size. Aim for polynomial time complexity algorithms that are efficient enough.

**IMPORTANT:**
* You MUST write all three plan outlines under the headings `## Plan Outline 1`, `## Plan Outline 2`, `## Plan Outline 3` in your response; otherwise, it will not be counted.
* **Multi-Start Mandate**: If your algorithm involves ANY randomness (random init, stochastic descent), your plan MUST explicitly mandate a "Multi-Start" loop (e.g., "Run optimization N=20 times, keep the best"). This is to eliminate variance.
* **Code-Ready**: Your plan MUST be detailed. Avoid vague terms like "adjust positions." Instead, say "apply `scipy.optimize.minimize` with method 'SLSQP'".
* **Decouple Structure & Parameters**: Prioritize plans that separate the "Hard Part" (finding the structure/topology) from the "Easy Part" (tuning parameters using a solver).

VERY IMPORTANT: Everything after the `### Final Child Solution Generation Plan` heading will be handed to the Phase 2 executor verbatim as the plan; it must be self-contained and must not refer back to the outlines above.
VERY IMPORTANT: The final generated plan MUST be a detailed plan, which is a series of executable steps. **Prioritize plans that decouple "Structure Finding" (Non-convex/Hard) from "Parameter Tuning" (Convex/Easy/Exact).**
VERY, VERY IMPORTANT: This is your last chance. To beat the baseline, your plan MUST be "Code-Ready".
- Avoid vague terms like "adjust positions" or "use an algorithm". Instead, say "apply a gradient descent step using loss function L = ..." or "use simulated annealing with T=100".
- Your plan must include a "Correction/Refinement" mechanism (e.g., an LP solver or post-processing step) to strictly enforce constraints and guarantee a score >= 1.0.

<Example>
### Final Child Solution Generation Plan

**Objective:** [Task Objective, e.g., Maximize Circle Radii Sum]

**Selected Outline:** [Name of the Algorithm, e.g., Multi-Start NLP with LP Refinement]

**Rationale for Selection:**
1.  Mathematically guarantees non-overlapping constraints via Linear Programming.
2.  Uses gradient-based search to escape local optima.

**Best Plan:**
1.  **Step 1: Define Mathematical Model & Helper Functions**
    * **Inputs**: Center positions $(x, y)$.
    * **Function**: `solve_exact_radii(centers)` using `scipy.optimize.linprog`.
    * **Constraints**: $r_i + r_j \\le dist(i, j)$ (No Overlap).
    * **Output**: Valid radii maximizing the sum for the given centers.

2.  **Step 2: Implement Main Optimization Loop (Multi-Start)**
    * **Algorithm**: `scipy.optimize.minimize` (Method: 'SLSQP').
    * **Objective**: Minimize $-1 \\times \\sum(radii)$.
    * **Loop**: Run 20 times with different random initial centers.
    * **Safety**: Inside the loop, implicitly call `solve_exact_radii` to ensure every step evaluates a VALID configuration.

3.  **Step 3: Post-Processing & Final "Safety Valve"**
    * **Logic**: Take the best result from the loop.
    * **Final Check**: Run `solve_exact_radii` one last time with high precision to ensure no floating-point violations.
    * **Fallback**: If optimization fails (e.g., success=False) or score < 1.0, return a known safe baseline (e.g., simple grid) to avoid crashing.

**Expected Performance Improvement:**
1.  Score >= 1.0 guaranteed by LP formulation.
</Example>

Begin your generation plan now. Write the three outlines, the comparison, and then the final best plan under the `### Final Child Solution Generation Plan` heading."""

# NOEMA (§1.2) ADAPTs vs EVOLVE_PLANNER_USER_PROMPT, in order: the
# `# Workspace` section is DROPPED; `{island_status_block}` is a new
# conditional slot (task 0061) after the `# Database` sentence; step 2 tool
# call -> "Review the database information provided above ..."; step 4 Write
# Tool file saves -> inline `## Plan Outline 1/2/3` headings (heading names
# are upstream's own, EVOLVE_PLANNER_SUMMARY_PROMPT) and its italic is
# inverted (write outlines out in full); step 8 loop -> single-pass revision;
# step 9 generate_final_answer -> the `### Final Child Solution Generation
# Plan` heading (lifted from upstream's own <Example>); IMPORTANT bullet 1
# file-save enforcement -> heading enforcement; the {workspace} VERY IMPORTANT
# line is repurposed as the self-containment rule (the extracted slice must
# not refer back to the outlines); closing line inverted (write, don't call).
# {task_info} <- domain_context; {parent_solution} <- lineage JSON;
# noema's custom-only {recent_block} is deliberately ABSENT (Decision #27).
# One rendering fix: upstream's un-raw string mangles the <Example>'s
# "$-1 \times" into a literal tab + "imes" at runtime (their SyntaxWarning
# confirms the accident); this constant renders the intended \times.

# ============================== END BORROWED =================================

# The exact heading the host slices on (upstream's own <Example> heading);
# extraction takes the LAST occurrence in the completion.
FINAL_PLAN_HEADING = "### Final Child Solution Generation Plan"
# Floor for the faithful planner completion (three outlines + comparison +
# expanded plan; design note §1.4).
FAITHFUL_PLANNER_MIN_TOKENS = 2048


def extract_final_plan(completion: str) -> Tuple[str, bool]:
    """Slice the executor-bound plan out of a faithful planner completion.

    The completion legitimately contains three outlines + a comparison before
    the plan; the executor must never see them. Rule: the text after the LAST
    occurrence of FINAL_PLAN_HEADING (the prompt's <Example> re-states the
    heading, so a completion may echo it more than once — the real plan is the
    final one). Missing heading -> (full stripped completion, False); the
    caller logs the fallback (shakedown gate 1 counts these).
    """
    idx = completion.rfind(FINAL_PLAN_HEADING)
    if idx < 0:
        return completion.strip(), False
    return completion[idx + len(FINAL_PLAN_HEADING):].strip(), True


class Planner:
    """Plan phase: builds the planning prompt and makes the one metered
    `pes.plan` call per mutation. Shared state lives on the module façade."""

    def __init__(self, module: "PESPlannerModule"):
        self._m = module

    async def plan(self, ctx: GenerationContext) -> Optional[str]:
        """One metered `pes.plan` call. Returns the stripped plan text, or
        None when the call failed or produced nothing (the iteration then
        runs unplanned). BudgetExhausted propagates (clean run stop).

        prompt_variant == "faithful" (task 0063): the LoongFlow-recast prompt,
        a max_tokens floor (outlines + comparison + plan must fit), and the
        last-heading extraction of the executor-bound plan slice. A raising
        island_bests_provider propagates out of advise() — fail loud: a broken
        provider is a host bug, and silently dropping the block would silently
        change the treatment mid-run (0061 verifier finding 9 posture)."""
        m = self._m
        if m.prompt_variant == "faithful":
            prompt = self._build_faithful_prompt(ctx)
            # task_info rides in the user prompt (upstream placement); no
            # domain-context system suffix in faithful mode.
            system_message = FAITHFUL_PLANNER_SYSTEM
            call_kwargs = {"max_tokens": self._faithful_max_tokens()}
        else:
            prompt = self._build_planning_prompt(ctx)
            system_message = PLANNER_SYSTEM
            if m.domain_context:
                system_message = f"{PLANNER_SYSTEM}\n\n# Problem Domain\n{m.domain_context}"
            call_kwargs = {}
        try:
            completion = await m.llm.generate_with_context(
                system_message=system_message,
                messages=[{"role": "user", "content": prompt}],
                tag="pes.plan",
                **call_kwargs,
            )
        except BudgetExhausted:
            raise  # clean run stop, same contract as the mutation account
        except Exception as e:
            logger.warning(f"PES planning call failed; iteration runs unplanned: {e}")
            return None
        completion = (completion or "").strip()
        if m.prompt_variant == "faithful" and completion:
            plan, extracted = extract_final_plan(completion)
            if not extracted:
                logger.warning(
                    "PES faithful planner: final-plan heading missing at iteration "
                    f"{ctx.iteration}; using the full completion (shakedown gate 1)"
                )
            elif not plan:
                # Heading present but nothing after it — a truncation symptom
                # gate 1 must also see (0063 verifier finding 5).
                logger.warning(
                    "PES faithful planner: empty plan slice after the heading at "
                    f"iteration {ctx.iteration}; iteration runs unplanned (shakedown gate 1)"
                )
            return plan or None
        return completion or None

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

    # ------------------------------------------------ faithful variant (0063)

    def _build_faithful_prompt(self, ctx: GenerationContext) -> str:
        """User prompt for the faithful variant (design note §1.3 mapping):
        {task_info} <- domain_context (moves from the system-message suffix to
        here, matching upstream placement); {parent_solution} <- lineage JSON
        with nulls for a fresh lineage; {island_num} <- provider length (the
        controller always injects the provider in a live run; without it the
        count degrades to the only island we can attest to);
        {island_status_block} <- the 0061 conditional block; noema's
        custom-only recent_block is deliberately absent (Decision #27)."""
        m = self._m
        parent = ctx.parent
        prior = m._plans.get(parent.id)
        parent_solution = {
            "generate_plan": prior["plan"] if prior else None,
            "solution": m._truncate(parent.code),
            "score": parent.fitness,
            "summary": prior.get("reflection") if prior else None,
        }
        return FAITHFUL_PLANNER_USER_TEMPLATE.format(
            task_info=m.domain_context or "None provided.",
            parent_solution=json.dumps(parent_solution, indent=2),
            database_block=self._database_block(ctx),
        )

    def _faithful_max_tokens(self) -> int:
        """Completion cap for the faithful plan call: at least
        FAITHFUL_PLANNER_MIN_TOKENS, so three outlines + comparison + expanded
        plan fit (design note §1.4). A configured cap above the floor is kept.
        The floor is sent explicitly even with no configured cap: local
        OpenAI-compatible servers may default an omitted max_tokens low enough
        to truncate before the final-plan heading (0063 verifier finding 1)."""
        configured = getattr(self._m.llm, "max_tokens", None)
        return max(configured or 0, FAITHFUL_PLANNER_MIN_TOKENS)

    # --------------------------------- global-perspective regions (0061/0080)

    def _database_block(self, ctx: GenerationContext) -> str:
        """The faithful planner's `# Database` section: how many regions exist,
        which one the parent sits in, and the best score in each.

        LoongFlow served this via Get_Memory_Status / Get_Best_Solutions; noema
        pre-injects it (task 0061). Task 0080 moved the data source from a
        host-injected `island_bests_provider` callable to the neutral
        `global_population.regions` snapshot, so PES no longer holds a callback
        into a concrete store.

        On islands the rendering is byte-identical to the verbatim LoongFlow
        text — that arm is the fidelity anchor. On any other topology the
        substrate's own region labels are used and the deviation is declared in
        `topology_adaptation` (never silently relabelled as islands). Regions
        are absent (a store that declares no `regions` capability, or an
        old-shaped fixture) → the section degrades to the parent's scope alone
        and the Global Perspective strategies stay verbatim but inert, which is
        the pre-0080 no-provider behavior.
        """
        snapshot = ctx.global_population
        regions = tuple(snapshot.regions) if snapshot else ()
        parent_scope = ctx.scope_id
        count = len(regions) if regions else int(parent_scope or 0) + 1

        if snapshot and snapshot.topology != "islands" and regions:
            # Declared adaptation: the substrate is not islands, so the noun and
            # the labels come from the substrate, not from LoongFlow's wording.
            here = self._region_label(regions, parent_scope)
            if here is None:
                # Global-scope substrate (tree): target_scope() is None, so no
                # region matches scope_id and the parent's location cannot be
                # named. State only what is true — descent — rather than a
                # location claim; richer wording is task 0082's decision.
                location = (
                    "The child solution will be generated directly from the "
                    "parent_solution."
                )
            else:
                location = (
                    f"The parent_solution is located in {here}, so the child "
                    f"solution will also be located in {here}."
                )
            lines = [f"The current database includes {count} regions. {location}"]
            scores = ", ".join(f"{r.label}: {r.best_fitness:.4f}" for r in regions)
            lines.append(f"Region status (best score per region): {scores}")
            return "\n".join(lines)

        # Islands (and the degraded no-regions path): LoongFlow verbatim.
        block = (
            f"The current database includes {count} islands. The parent_solution "
            f"is located in island_{parent_scope}, so the child solution will also "
            f"be located in island_{parent_scope}."
        )
        if not regions:
            return f"{block}\n"
        scores = ", ".join(f"{r.label}: {r.best_fitness:.4f}" for r in regions)
        return f"{block}\nIsland status (best score per island): {scores}"

    @staticmethod
    def _region_label(regions: Sequence["RegionSummary"], scope: Any) -> Optional[str]:
        """The label of the region whose scope matches, or None — never a
        synthesized name (str(None) rendered "located in None" on the tree)."""
        for region in regions:
            if region.scope == scope:
                return region.label
        return None

    def topology_adaptation(self, ctx: GenerationContext) -> Optional[str]:
        """The declared prompt deviation for this context, or None on the native
        islands substrate. Recorded in `Advice.attribution` so a run's evidence
        shows which cells rendered an adapted prompt."""
        snapshot = ctx.global_population
        if not snapshot or not snapshot.regions or snapshot.topology == "islands":
            return None
        return f"region_worded_database_block:{snapshot.topology}"

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
