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

Documented deviations from the released code (PLAN.md section 2.2):
1. Credit assignment actually works here. The original ran offspring generation
   in joblib subprocesses, so tip-stat updates mutated worker-local copies of
   the pool and were lost; noema runs the mechanism in-process.
2. Fitness is MAXIMIZED (openevolve convention); the original minimized. The
   navigator takes maximize=True and the effectiveness formula is mirrored.
3. The original's exception-path penalty (-0.8) was dead code (the offspring
   dict it inspected had no metadata); the live failure semantics — evaluation
   failure scores effectiveness -0.5 — are what report_result implements.
4. All magic numbers (pool size, k tips, extraction probability, ...) are
   config fields with the original values as defaults.
"""

import logging
from typing import Any, Dict, List, Optional

from noema.coordination.base import (
    Advice,
    CoordinationModule,
    GenerationContext,
    SamplingRequest,
    SelectionContext,
)
from noema.coordination.hifo.evolutionary_navigator import EvolutionaryNavigator
from noema.coordination.hifo.insight_pool import InsightPool
from noema.views import ProgramView

# EoH operator taxonomy for regime steering (task 0072 F3): e-operators produce
# divergent/new forms (exploration); m-operators refine/tune existing ones
# (exploitation). HiFo's coarse regime biases the draw toward the matching family.
EXPLORATION_OPERATORS = ("e1", "e2")
EXPLOITATION_OPERATORS = ("m1", "m2", "m3")

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
        extraction_probability:   chance per generation tick of running the
                                  insight-extraction LLM call (0.8)
        failure_effectiveness:    effectiveness for failed offspring (-0.5)
        max_code_chars:           code truncation for extraction prompts (1000/800)
        min_tip_length:           minimum accepted extracted-tip length (10)
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

        self.insight_pool = InsightPool(
            max_size=cfg.get("pool_max_size", 30),
            initial_tips=cfg.get("initial_tips"),
            rng=self.rng,
        )
        self.navigator = EvolutionaryNavigator(maximize=True, rng=self.rng)
        # F2 cadence fix (task 0072): the navigator's own rolling best-fitness
        # history, advanced once per advise() (i.e. per offspring). The host's
        # ctx.best_fitness_history only advances at the generation tick, so
        # reading it per mutation made improvement==0 on every intra-generation
        # call and the exploitation counter unreachable. Observing the global
        # best per offspring restores the source's read/write coupling and makes
        # the 3/2 thresholds a substrate-independent per-offspring unit (F4).
        self._nav_best_history: List[float] = []
        self._nav_history_cap: int = cfg.get("nav_history_cap", 50)
        # F3 regime→operator steering (task 0072, Decision #45). HiFo emits an
        # operator request ONLY when told the menu is on (`operators` non-empty),
        # so a hifo run without the menu is byte-identical to before. The regime
        # from the previous advise() biases the draw (sampling_request runs before
        # advise in the loop, so the current regime isn't computed yet — a one-step
        # lag that is invisible in practice because regimes are sticky).
        self.operators: List[str] = list(cfg.get("operators", []))
        self.regime_bias: float = cfg.get("regime_bias", 3.0)
        self._exploration_ops = tuple(cfg.get("exploration_operators", EXPLORATION_OPERATORS))
        self._exploitation_ops = tuple(cfg.get("exploitation_operators", EXPLOITATION_OPERATORS))
        self._last_regime: str = "balanced"

    # -------------------------------------------------- pre-selection (F3)

    def sampling_request(self, ctx: SelectionContext) -> SamplingRequest:
        """Bias the mutation operator toward the current regime (task 0072 F3).

        Emits a request ONLY when the menu is on (`operators` configured); without
        it this is the inherited no-op and a hifo run is byte-identical to before.
        The regime is a soft bias realized as a weighted draw over the configured
        operators (exploration favors the divergent e-family; exploitation the
        refining m-family; balanced is uniform), using the module RNG so it stays
        deterministic. The controller honors the single `operator` key exactly as
        it does for the bandit and records requested/honored/ignored.
        """
        if not self.operators:
            return SamplingRequest()
        weights = [self._operator_weight(op) for op in self.operators]
        chosen = self.rng.choices(self.operators, weights=weights, k=1)[0]
        return SamplingRequest(hints={"operator": chosen})

    def _operator_weight(self, operator: str) -> float:
        if self._last_regime == "exploration":
            return self.regime_bias if operator in self._exploration_ops else 1.0
        if self._last_regime == "exploitation":
            return self.regime_bias if operator in self._exploitation_ops else 1.0
        return 1.0  # balanced → uniform

    # ------------------------------------------------------------- advise

    async def advise(self, ctx: GenerationContext) -> Advice:
        # Same cadence as the original: guidance recomputed per offspring
        # (InterfaceEC._get_alg), tips drawn per offspring
        self.insight_pool.update_generation(ctx.generation)
        # F2 fix (task 0072): feed the navigator a per-offspring best-fitness
        # signal from the current global best, not the tick-cadenced host history.
        # avg/diversity remain the host histories (secondary regime modifiers;
        # per-offspring diversity is ill-defined and not the F2 defect).
        best = ctx.global_population.best_program if ctx.global_population else None
        if best is not None:
            self._nav_best_history.append(best.fitness)
            if len(self._nav_best_history) > self._nav_history_cap:
                self._nav_best_history = self._nav_best_history[-self._nav_history_cap:]
        regime, directive = self.navigator.get_guidance(
            best_fitness_history=self._nav_best_history,
            avg_fitness_history=ctx.avg_fitness_history,
            diversity_history=ctx.diversity_history,
        )
        # Remember the regime so the NEXT iteration's sampling_request can bias the
        # operator toward it (F3). advise runs after sampling_request in the loop.
        self._last_regime = regime
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
    ) -> None:
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
        # Original: extraction runs with probability 0.8 per generation step
        if self.rng.random() >= self.extraction_probability:
            return
        top_programs = ctx.local_population.top_programs
        if self.llm is None or not top_programs:
            return
        await self._extract_insights(top_programs)

    async def _extract_insights(self, top_programs: List[ProgramView]) -> None:
        # BORROWED logic — adapted from HiFo-Prompt
        # (hifo_interface_EC.py, InterfaceEC.extract_insights_from_population).
        # The original summarized the top 30% of its population by their
        # one-sentence 'algorithm' descriptions, falling back to truncated
        # code; noema's analog of the description is changes_description.
        prompt = EXTRACTION_PROMPT_HEADER
        for i, program in enumerate(top_programs):
            description = (program.changes_description or "").strip()
            if description and len(description) > 8:
                content_to_analyze = f"{description}"
            else:
                code_to_analyze = program.code
                if len(code_to_analyze) > self.max_code_chars:
                    code_to_analyze = (
                        code_to_analyze[: int(self.max_code_chars * 0.8)]
                        + "...\n# (truncated for brevity)"
                    )
                content_to_analyze = f"{code_to_analyze}"
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
            "last_regime": self._last_regime,
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.insight_pool.load_state_dict(state["insight_pool"])
        self.navigator.load_state_dict(state["navigator"])
        self._nav_best_history = [float(x) for x in state.get("nav_best_history", [])]
        self._last_regime = state.get("last_regime", "balanced")

    def log_snapshot(self) -> Dict[str, Any]:
        # Mirrors the per-generation hifo_prompt_log written by the original
        # outer loop (methods/hifo/hifo.py)
        return {
            "current_insight_count": len(self.insight_pool.tips),
            "recent_insights": list(self.insight_pool.tips)[-3:],
            "navigator_guidance": self.navigator.last_guidance,
            "pool_stats": self.insight_pool.get_stats_summary(),
        }
