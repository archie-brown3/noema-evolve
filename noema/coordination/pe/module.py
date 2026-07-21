"""Punctuated Equilibrium coordination arm (task 0109).

Ported in structure from LEVI (https://github.com/ttanv/levi, MIT (c) 2025
Temoor Tanveer), ``levi/equilibrium/equilibrium.py``.  Fires every ``interval``
generation ticks: clusters the current elites by behaviour, generates a
paradigm shift + variants with the coordination LLM, and RETURNS them as an
``Intervention`` for the host to evaluate and insert (spec §3).

NOEMA adaptations vs. the donor (see [[Punctuated Equilibrium ... Spec]] §6):
- proposes programs, never evaluates or inserts them (host does — metering);
- clusters the elites visible in the neutral global snapshot, re-extracting
  behaviour from their code, so no store internals are touched;
- KMeans is seeded from the module RNG (donor uses random_state=None) — determinism;
- ``advise`` is a no-op, so the mutation prompt stays byte-identical to null;
- one coordination LLM, all paradigm + variant spend billed to the coordination
  account (spec §8 recommendation).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.cluster import KMeans

from openevolve.utils.code_utils import parse_full_rewrite
from noema.coordination.base import (
    Advice,
    CoordinationModule,
    GenerationContext,
    Intervention,
    Outcome,
    ProposedProgram,
)
from noema.coordination.pe.prompts import paradigm_shift_prompt, variant_prompt
from noema.cvt_behavior import DEFAULT_FEATURE_BOUNDS, BehaviorExtractor

logger = logging.getLogger(__name__)

# Fixed bounds -> deterministic behaviour vectors for clustering (shared with
# the CVT store so a program clusters the same way it is celled).
_CLUSTER_FEATURES = ("math_operators", "loop_nesting_max", "comprehension_count", "range_max_arg")
_CLUSTER_BOUNDS = {f: DEFAULT_FEATURE_BOUNDS[f] for f in _CLUSTER_FEATURES}


class PunctuatedEquilibriumModule(CoordinationModule):
    """Periodic paradigm-shift injection (LEVI). A compound arm: coordination
    LLM authors whole programs the host evaluates and inserts."""

    def __init__(self, config=None, llm=None, rng=None):
        super().__init__(config, llm, rng)
        cfg = self.config
        self.interval: int = int(cfg.get("interval", 10))
        self.n_clusters: int = int(cfg.get("n_clusters", 3))
        self.n_variants: int = int(cfg.get("n_variants", 3))
        self.temperature: float = float(cfg.get("temperature", 1.0))
        self.domain_context: str = cfg.get("domain_context", "")
        self.language: str = cfg.get("language", "python")
        self._extractor = BehaviorExtractor(list(_CLUSTER_FEATURES))
        self._extractor.set_fixed_bounds(dict(_CLUSTER_BOUNDS))
        self._trigger_count: int = 0

    # PE does not touch the mutation prompt — null-identical per mutation.
    async def advise(self, ctx: GenerationContext) -> Advice:
        return Advice()

    def report_result(self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED) -> None:
        return None

    def _cluster_representatives(self, elites) -> List:
        """Best-fitness elite per behavioural cluster (KMeans, seeded)."""
        vectors = np.array(
            [self._extractor.extract(e.code).to_array(list(_CLUSTER_FEATURES)) for e in elites]
        )
        k = min(self.n_clusters, len(elites))
        seed = self.rng.randint(0, 2**31 - 1)  # deterministic given module RNG
        labels = KMeans(n_clusters=k, n_init=1, random_state=seed).fit_predict(vectors)
        reps: Dict[int, Any] = {}
        for elite, label in zip(elites, labels):
            label = int(label)
            if label not in reps or elite.fitness > reps[label].fitness:
                reps[label] = elite
        return sorted(reps.values(), key=lambda e: -e.fitness)

    async def _generate(self, prompt: str, tag: str) -> Optional[str]:
        response = await self.llm.generate(prompt, tag=tag, temperature=self.temperature)
        code = parse_full_rewrite(response, self.language)
        return code or None

    async def on_generation_end(self, ctx: GenerationContext) -> Optional[Intervention]:
        if self.llm is None:
            return None
        if self.interval <= 0 or ctx.iteration == 0 or ctx.iteration % self.interval != 0:
            return None
        elites = list(ctx.global_population.top_programs)
        if len(elites) < self.n_clusters:
            return None  # not enough behavioural diversity yet to cluster

        self._trigger_count += 1
        reps = self._cluster_representatives(elites)
        anchor = reps[0]  # highest-fitness representative

        proposals: List[ProposedProgram] = []
        paradigm_code = await self._generate(
            paradigm_shift_prompt(self.domain_context, [(e.code, e.fitness) for e in reps]),
            tag="pe.paradigm_shift",
        )
        if paradigm_code:
            proposals.append(
                ProposedProgram(code=paradigm_code, origin="paradigm_shift", parent_id=anchor.id)
            )
            seed_code, seed_score = paradigm_code, anchor.fitness
        else:
            seed_code, seed_score = anchor.code, anchor.fitness

        for _ in range(self.n_variants):
            variant_code = await self._generate(
                variant_prompt(self.domain_context, seed_code, seed_score),
                tag="pe.variant",
            )
            if variant_code:
                proposals.append(
                    ProposedProgram(code=variant_code, origin="variant", parent_id=anchor.id)
                )

        if not proposals:
            return None
        logger.info(
            "[PE] trigger #%d at iteration %d: %d proposals from %d clusters",
            self._trigger_count, ctx.iteration, len(proposals), len(reps),
        )
        return Intervention(proposals=tuple(proposals))

    def state_dict(self) -> Dict[str, Any]:
        return {"trigger_count": self._trigger_count}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self._trigger_count = int(state.get("trigger_count", 0))

    def log_snapshot(self) -> Dict[str, Any]:
        return {"pe_trigger_count": self._trigger_count}
