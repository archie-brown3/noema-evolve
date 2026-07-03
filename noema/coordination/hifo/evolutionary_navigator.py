# ============================================================================
# BORROWED CODE — copied from HiFo-Prompt
# Source: https://github.com/Challenger-XJTU/HiFo-Prompt
#         hifo/src/hifo/methods/hifo/evolutionary_navigator.py
# This is the "foresight" half of the HiFo-Prompt coordination mechanism: a
# pure-heuristic (no LLM) navigator that tracks stagnation/improvement from
# fitness history and emits a search regime (exploration / exploitation /
# balanced) plus a design directive drawn from fixed per-regime lists.
#
# The code is kept as close to the original as practical so the transplant is
# auditable. Local modifications are marked with "# NOEMA:" comments; they are:
#   1. injectable RNG (the original used the global `random` module),
#   2. a `maximize` flag — the original assumed MINIMIZED objectives
#      (improvement = last_best - current); noema fitness is maximized,
#   3. state_dict/load_state_dict for checkpointing,
#   4. dropped an unused `import numpy as np`.
# ============================================================================

import random


class EvolutionaryNavigator:

    # NOEMA: added `maximize` and `rng` parameters (original signature was
    # (self) with minimization semantics and the global random module)
    def __init__(self, maximize=True, rng=None):
        self.stagnation_count = 0
        self.improvement_count = 0
        self.last_best_fitness = None
        self.last_guidance = None
        self.learning_rate = 0.1
        self.maximize = maximize  # NOEMA: fitness sign convention
        self.rng = rng if rng is not None else random  # NOEMA: injectable RNG

        self.regimes = ["exploration", "exploitation", "balanced"]

        self.design_directives = {
            "general": [
                "optimizing objective function evaluation criteria",
                "considering long-term impact of current decisions",
                "balancing local optimality with global search strategies",
                "improving algorithm robustness across different problem instances",
                "managing computational complexity and time efficiency",
            ],
            "exploitation": [
                "refining core evaluation and scoring functions",
                "fine-tuning critical algorithm parameters and thresholds",
                "optimizing established successful strategies and patterns",
                "reducing unnecessary computational overhead and redundancy",
                "improving precision of existing heuristics and rules",
            ],
            "exploration": [
                "exploring novel solution construction methodologies",
                "investigating alternative problem decomposition approaches",
                "introducing new randomization or adaptive mechanisms",
                "considering completely different algorithmic paradigms",
                "experimenting with hybrid strategy combinations",
            ],
        }

    def get_guidance(
        self, pop=None, best_fitness_history=None, avg_fitness_history=None, diversity_history=None
    ):
        regime = "balanced"
        design_directive = self.rng.choice(  # NOEMA: was random.choice (also below)
            self.design_directives["general"]
        )

        if not best_fitness_history or len(best_fitness_history) < 2:
            return regime, design_directive

        current_best = best_fitness_history[-1]
        if self.last_best_fitness is not None:
            # NOEMA: the original computed `improvement = self.last_best_fitness
            # - current_best` (minimization: lower fitness is better). noema's
            # fitness is maximized, so the sign flips when maximize=True.
            if self.maximize:
                improvement = current_best - self.last_best_fitness
            else:
                improvement = self.last_best_fitness - current_best

            if improvement <= 1e-4:
                self.stagnation_count += 1
                self.improvement_count = 0
            else:
                self.improvement_count += 1
                self.stagnation_count = 0

        self.last_best_fitness = current_best

        low_diversity = False
        if diversity_history and len(diversity_history) > 0:
            if diversity_history[-1] < 0.3:
                low_diversity = True

        if self.stagnation_count >= 3:
            regime = "exploration"
            design_directive = self.rng.choice(self.design_directives["exploration"])

        elif low_diversity:
            regime = "exploration"
            design_directive = self.rng.choice(self.design_directives["exploration"])

        elif self.improvement_count >= 2:
            regime = "exploitation"
            design_directive = self.rng.choice(self.design_directives["exploitation"])

        else:
            weights = [0.25, 0.25, 0.5]
            regime = self.rng.choices(self.regimes, weights=weights, k=1)[0]

            if regime == "exploration":
                design_directive = self.rng.choice(self.design_directives["exploration"])
            elif regime == "exploitation":
                design_directive = self.rng.choice(self.design_directives["exploitation"])
            else:
                directive_pool = (
                    self.design_directives["general"]
                    + self.design_directives["exploitation"]
                    + self.design_directives["exploration"]
                )
                design_directive = self.rng.choice(directive_pool)

        self.last_guidance = (regime, design_directive)

        return regime, design_directive

    # ------------------------------------------------------------------
    # NOEMA: checkpointing support (not in the original)
    # ------------------------------------------------------------------

    def state_dict(self):
        return {
            "stagnation_count": self.stagnation_count,
            "improvement_count": self.improvement_count,
            "last_best_fitness": self.last_best_fitness,
            "last_guidance": list(self.last_guidance) if self.last_guidance else None,
            "maximize": self.maximize,
        }

    def load_state_dict(self, state):
        self.stagnation_count = state["stagnation_count"]
        self.improvement_count = state["improvement_count"]
        self.last_best_fitness = state["last_best_fitness"]
        self.last_guidance = tuple(state["last_guidance"]) if state["last_guidance"] else None
        self.maximize = state["maximize"]
