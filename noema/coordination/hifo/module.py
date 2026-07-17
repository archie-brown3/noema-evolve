"""
HiFoPromptModule: the HiFo-Prompt mechanism behind noema's CoordinationModule
interface.

Mapping from the released HiFo-Prompt code (hifo_interface_EC.InterfaceEC glue):
- advise()            <- InterfaceEC._get_alg guidance + insight selection, and
                         the prompt-suffix injection from hifo_evolution.py
- report_result()     <- InterfaceEC.update_insight_feedback /
                         calculate_insight_effectiveness (credit assignment)
- on_generation_end() <- InterfaceEC.extract_insights_from_population (the
                         mechanism's only LLM call) + generation bookkeeping

Documented deviations from the released code (PLAN.md section 2.2; authorities in
the vault's HiFo Fidelity Contract — 2026-07, Decisions #50-#54):
1. Credit assignment actually works here. The original ran offspring generation
   in joblib subprocesses, so tip-stat updates mutated worker-local copies of
   the pool and were lost; noema runs the mechanism in-process.
2. Fitness is MAXIMIZED (openevolve convention); the original minimized. The
   navigator takes maximize=True and the effectiveness formula is mirrored.
3. The original's exception-path penalty (-0.8) was dead code (the offspring
   dict it inspected had no metadata). Its LIVE failure semantics were: an
   exception anywhere in generate/evaluate -> tips receive no update at all;
   an evaluated-but-None objective -> -0.5. report_result mirrors that via the
   Outcome discriminator (Decision #53): NO_PROGRAM / EVAL_ERROR -> no update,
   remaining failure classes -> failure_effectiveness.
4. All magic numbers (pool size, k tips, extraction probability, ...) are
   config fields with the original values as defaults.
5. The navigator reads a module-internal best-fitness history advanced once per
   advise() from the global best (Decision #50, repairing the source's broken
   read/write cadence — its shipped regime detection never functions: counters
   are lost to joblib copies when parallel and fed an unchanged snapshot when
   sequential). The source defect is documented as a finding, not reproduced.
"""

import logging
import math
from typing import Any, Dict, List, Optional

from noema.coordination.base import Advice, CoordinationModule, GenerationContext, Outcome
from noema.coordination.hifo.evolutionary_navigator import EvolutionaryNavigator
from noema.coordination.hifo.insight_pool import InsightPool
from noema.views import ProgramView

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BORROWED prompt phrasing — copied from HiFo-Prompt
# (hifo/src/hifo/methods/hifo/hifo_evolution.py, get_prompt_* suffix blocks)
# ---------------------------------------------------------------------------
INSIGHTS_PREFIX = "Consider these successful design principles I've observed recently:"
DIRECTIVE_TEMPLATE = "For this task, please pay special attention to: {directive}"
REGIME_LINES = {
    "exploration": (
        "Try to explore a significantly different approach compared to conventional solutions."
    ),
    "exploitation": (
        "Focus on refining and optimizing the most effective patterns in optimization algorithms."
    ),
    "balanced": "Strike a balance between novel ideas and proven effective techniques.",
}

# ---------------------------------------------------------------------------
# BORROWED prompt phrasing — copied from HiFo-Prompt
# (hifo/src/hifo/methods/hifo/hifo_interface_EC.py,
#  InterfaceEC.extract_insights_from_population)
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT_HEADER = (
    "The following are core descriptions of high-performance optimization algorithms "
    "evolved recently:\n"
)
EXTRACTION_PROMPT_FOOTER = (
    "\nPlease extract 1-2 concise, generic, and performance-positive [design principles]"
    " or [effective patterns] from the above algorithms."
    "\nThese principles should be applicable to various combinatorial optimization problems,"
    " not just the specific problem domain."
    "\nWhen formulating these principles, it is essential to draw insights from *both* the"
    " conceptual natural language descriptions *and* their corresponding code implementations."
    " Focus on identifying the underlying strategic design choices and algorithmic"
    " methodologies rather than superficial characteristics or specific implementation"
    " minutiae."
    "\nEach principle/pattern should be expressed as an independent sentence in the following"
    " format:"
    "\n- Balance local optimization with global solution structure when making decisions."
    "\n- Prioritize choices that maintain flexibility for future decision-making steps."
    "\n- Implement adaptive mechanisms that respond to problem instance characteristics."
)


class HiFoPromptModule(CoordinationModule):
    """
    Insight pool (hindsight) + evolutionary navigator (foresight), per
    HiFo-Prompt. The coordination LLM (self.llm, bound to the ledger's
    "coordination" account) is used only for insight extraction.

    Config keys (defaults are the released HiFo-Prompt values):
        pool_max_size:            insight pool capacity (30)
        tips_per_prompt:          k tips injected per mutation (3)
        tip_strategy:             pool selection strategy ("adaptive")
        initial_tips:             seed tips (None = HiFo's defaults)
        extraction_probability:   chance per extraction roll of running the
                                  insight-extraction LLM call (0.8)
        failure_effectiveness:    effectiveness for failed-but-evaluated
                                  offspring (-0.5); infrastructure failures
                                  (NO_PROGRAM/EVAL_ERROR) skip credit entirely
        max_code_chars:           code truncation for extraction prompts (1000/800)
        min_tip_length:           minimum accepted extracted-tip length (10)
        nav_history_cap:          module-internal navigator best-history cap (50,
                                  the source's history cap)
        extraction_interval_offspring: offspring per extraction roll (5, the
                                  source's per-operator-application cadence
                                  translated per-offspring; None = per-tick)
        extraction_min_population: minimum scope size for extraction (3)
        extraction_top_fraction:  summarized slice of the scope (0.3)
        extraction_input:         "thoughts" (source-faithful) or "thoughts+code"
                                  (labeled variant, Decision #54)
    """

    def __init__(self, config=None, llm=None, rng=None):
        super().__init__(config=config, llm=llm, rng=rng)
        cfg = self.config
        self.tips_per_prompt: int = cfg.get("tips_per_prompt", 3)
        self.tip_strategy: str = cfg.get("tip_strategy", "adaptive")
        self.extraction_probability: float = cfg.get("extraction_probability", 0.8)
        self.failure_effectiveness: float = cfg.get("failure_effectiveness", -0.5)
        self.max_code_chars: int = cfg.get("max_code_chars", 1000)
        self.min_tip_length: int = cfg.get("min_tip_length", 10)
        # Decision #52: the source rolled p=0.8 once per operator-application
        # (= once per pop_size offspring, default 5). Translated per-offspring so
        # the cadence is substrate-independent. None = legacy per-tick single roll.
        self.extraction_interval: Optional[int] = cfg.get("extraction_interval_offspring", 5)
        # Source gates (hifo_interface_EC.py:298-303): >=3 individuals, top 30%.
        self.extraction_min_population: int = cfg.get("extraction_min_population", 3)
        self.extraction_top_fraction: float = cfg.get("extraction_top_fraction", 0.3)
        # Decision #54: "thoughts" = source-faithful (description preferred, code
        # fallback); "thoughts+code" = labeled variant sending both.
        self.extraction_input: str = cfg.get("extraction_input", "thoughts")
        self._offspring_seen: int = 0
        self._extraction_cursor: int = 0

        self.insight_pool = InsightPool(
            max_size=cfg.get("pool_max_size", 30),
            initial_tips=cfg.get("initial_tips"),
            rng=self.rng,
        )
        self.navigator = EvolutionaryNavigator(maximize=True, rng=self.rng)
        # Decision #50: the navigator's own best-fitness history, advanced once
        # per advise() (= per offspring) from the global best. The host's
        # ctx.best_fitness_history only advances at the generation tick, so
        # feeding it per-mutation made improvement==0 on every intra-tick call
        # and the exploitation trigger unreachable — the same degeneracy the
        # source exhibits (see docstring deviation 5). Cap = the source's 50.
        self._nav_best_history: List[float] = []
        self._nav_history_cap: int = cfg.get("nav_history_cap", 50)

    # ------------------------------------------------------------- advise

    async def advise(self, ctx: GenerationContext) -> Advice:
        # Same cadence as the original: guidance recomputed per offspring
        # (InterfaceEC._get_alg), tips drawn per offspring
        self._offspring_seen += 1  # drives the Decision #52 extraction windows
        self.insight_pool.update_generation(ctx.generation)
        # Decision #50: observe the global best per offspring so the navigator's
        # read cadence matches its write cadence (avg/diversity remain the host
        # tick histories — secondary regime modifiers; per-offspring diversity
        # is ill-defined and was not part of the source defect).
        best = ctx.global_population.best_program if ctx.global_population else None
        if best is not None:
            self._nav_best_history.append(best.fitness)
            if len(self._nav_best_history) > self._nav_history_cap:
                self._nav_best_history = self._nav_best_history[-self._nav_history_cap :]
        regime, directive = self.navigator.get_guidance(
            best_fitness_history=self._nav_best_history,
            avg_fitness_history=ctx.avg_fitness_history,
            diversity_history=ctx.diversity_history,
        )
        insights = self.insight_pool.get_tips(k=self.tips_per_prompt, strategy=self.tip_strategy)

        # Assemble the three suffix blocks exactly as hifo_evolution.py appends
        # them to its operator prompts (insights, directive, regime line)
        parts: List[str] = []
        if insights:
            parts.append(INSIGHTS_PREFIX + "\n" + "\n".join(f"- {tip}" for tip in insights))
        if directive:
            parts.append(DIRECTIVE_TEMPLATE.format(directive=directive))
        if regime in REGIME_LINES:
            parts.append(REGIME_LINES[regime])

        return Advice(
            prompt_block="\n".join(parts),
            attribution={
                "insights": insights,
                "design_directive": directive,
                "regime": regime,
            },
        )

    # ------------------------------------------------------ credit assignment

    def report_result(
        self,
        ctx: GenerationContext,
        child: Optional[ProgramView],
        attribution: Dict[str, Any],
        eval_failed: bool,
        *,
        outcome: Outcome = Outcome.ACCEPTED,
    ) -> None:
        # Decision #53: mirror the source's LIVE failure semantics. In the
        # original, an exception anywhere in generate/evaluate meant tips got no
        # credit update at all (the -0.8 penalty on that path was dead code);
        # only an evaluated offspring — including one whose objective came back
        # None — reached the effectiveness formula. NO_PROGRAM / EVAL_ERROR are
        # exactly that exception class, so they skip credit entirely rather than
        # punishing tips for infrastructure failures they did not cause.
        if outcome in (Outcome.NO_PROGRAM, Outcome.EVAL_ERROR):
            return
        insights = attribution.get("insights") or []
        if not insights:
            return
        effectiveness = self._calculate_insight_effectiveness(
            child, ctx.local_population.fitnesses, eval_failed
        )
        for tip in insights:
            self.insight_pool.update_tip_stats(tip, effectiveness)
        logger.debug(
            f"HiFo credit assignment: effectiveness={effectiveness:.3f} "
            f"applied to {len(insights)} insights"
        )

    def _calculate_insight_effectiveness(
        self,
        child: Optional[ProgramView],
        population_fitnesses: List[float],
        eval_failed: bool,
    ) -> float:
        # BORROWED logic — adapted from HiFo-Prompt
        # (hifo_interface_EC.py, InterfaceEC.calculate_insight_effectiveness).
        # NOEMA: mirrored for MAXIMIZED fitness. The original minimized:
        # best = min(pop), worst = max(pop),
        # normalized = (worst - offspring) / (worst - best).
        # Here best = max(pop), worst = min(pop),
        # normalized = (offspring - worst) / (best - worst) — same [0, 1]
        # scale where 1 means "at the population best".
        if child is None or eval_failed:
            return self.failure_effectiveness  # original: -0.5 for objective None

        if not population_fitnesses:
            return 0.0

        offspring_fitness = child.fitness
        population_best = max(population_fitnesses)
        population_worst = min(population_fitnesses)
        population_avg = sum(population_fitnesses) / len(population_fitnesses)

        if population_worst == population_best:
            return 0.1

        normalized_performance = (offspring_fitness - population_worst) / (
            population_best - population_worst
        )

        if offspring_fitness >= population_best:
            effectiveness = 0.8 + 0.2 * normalized_performance
        elif offspring_fitness >= population_avg:
            effectiveness = 0.2 + 0.6 * normalized_performance
        else:
            effectiveness = -0.3 + 0.5 * normalized_performance

        return max(-1.0, min(1.0, effectiveness))

    # ------------------------------------------------------ insight extraction

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        self.insight_pool.update_generation(ctx.generation)
        # Decision #52 cadence: one p=0.8 roll per completed
        # extraction_interval_offspring window since the last tick (the source
        # rolled once per operator-application, hifo_interface_EC.py:264). The
        # LLM call itself stays confined to this hook — a tick may therefore
        # run several extractions, matching the source's up-to-n_op per
        # generation. None = legacy single roll per tick.
        if self.extraction_interval is None:
            rolls = 1
        else:
            windows = (
                self._offspring_seen - self._extraction_cursor
            ) // self.extraction_interval
            self._extraction_cursor += windows * self.extraction_interval
            rolls = windows
        hits = sum(1 for _ in range(rolls) if self.rng.random() < self.extraction_probability)
        if hits == 0 or self.llm is None:
            return
        # Source gates (hifo_interface_EC.py:298-303): the scope needs >= 3
        # individuals; the summarized slice is its top 30% (min 1).
        scope_size = max(
            len(ctx.local_population.fitnesses), len(ctx.local_population.top_programs)
        )
        if scope_size < self.extraction_min_population:
            return
        top_programs = list(ctx.local_population.top_programs)
        if not top_programs:
            return
        slice_n = max(1, math.ceil(self.extraction_top_fraction * scope_size))
        top_slice = top_programs[:slice_n]
        for _ in range(hits):
            await self._extract_insights(top_slice)

    async def _extract_insights(self, top_programs: List[ProgramView]) -> None:
        # BORROWED logic — adapted from HiFo-Prompt
        # (hifo_interface_EC.py, InterfaceEC.extract_insights_from_population).
        # The original summarized the top 30% of its population by their
        # one-sentence 'algorithm' descriptions, falling back to truncated
        # code; noema's analog of the description is changes_description.
        prompt = EXTRACTION_PROMPT_HEADER
        for i, program in enumerate(top_programs):
            description = (program.changes_description or "").strip()
            has_description = bool(description) and len(description) > 8
            code_snippet = None
            if not has_description or self.extraction_input == "thoughts+code":
                code_snippet = program.code
                if len(code_snippet) > self.max_code_chars:
                    code_snippet = (
                        code_snippet[: int(self.max_code_chars * 0.8)]
                        + "...\n# (truncated for brevity)"
                    )
            if has_description and code_snippet is not None:
                # "thoughts+code" (Decision #54): the one mode that makes the
                # footer's both-inputs claim true — the source only ever sent
                # one of the two per individual.
                content_to_analyze = f"{description}\n{code_snippet}"
            elif has_description:
                content_to_analyze = description
            else:
                content_to_analyze = code_snippet
            prompt += f"{i+1}. Algorithm: {content_to_analyze}\n"
        prompt += EXTRACTION_PROMPT_FOOTER

        # BudgetExhausted propagates to the controller (clean stop); other LLM
        # failures are swallowed like the original's try/except
        from noema.budget.ledger import BudgetExhausted

        try:
            response = await self.llm.generate(prompt, tag="hifo.extract_insights")
        except BudgetExhausted:
            raise
        except Exception as e:
            logger.warning(f"HiFo insight extraction failed: {e}")
            return

        # BORROWED parsing — lines starting with '-' become candidate tips
        insight_items = []
        for line in (response or "").split("\n"):
            if line.strip().startswith("-"):
                insight_items.append(line.strip()[2:].strip())

        added = 0
        for item in insight_items:
            if item and len(item) > self.min_tip_length:
                if self.insight_pool.add_tip(item, tags=["extracted", "high_performance"]):
                    added += 1
        logger.debug(f"HiFo extracted {len(insight_items)} insight items, added {added}")

    # ----------------------------------------------------------- persistence

    def state_dict(self) -> Dict[str, Any]:
        return {
            "insight_pool": self.insight_pool.state_dict(),
            "navigator": self.navigator.state_dict(),
            "nav_best_history": list(self._nav_best_history),
            "offspring_seen": self._offspring_seen,
            "extraction_cursor": self._extraction_cursor,
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.insight_pool.load_state_dict(state["insight_pool"])
        self.navigator.load_state_dict(state["navigator"])
        self._nav_best_history = [float(x) for x in state.get("nav_best_history", [])]
        self._offspring_seen = int(state.get("offspring_seen", 0))
        self._extraction_cursor = int(state.get("extraction_cursor", 0))

    def log_snapshot(self) -> Dict[str, Any]:
        # Mirrors the per-generation hifo_prompt_log written by the original
        # outer loop (methods/hifo/hifo.py)
        return {
            "current_insight_count": len(self.insight_pool.tips),
            "recent_insights": list(self.insight_pool.tips)[-3:],
            "navigator_guidance": self.navigator.last_guidance,
            "pool_stats": self.insight_pool.get_stats_summary(),
        }
