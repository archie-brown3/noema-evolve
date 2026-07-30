"""ReEvo-derived short-term reflection, adapted as a coordination-only arm.

This module deliberately does not select parents, crossover, evaluate, or
admit programs.  It observes Noema's already-selected parent and a local
population snapshot, then contributes one donor-shaped verbal gradient to the
ordinary host mutation prompt.  Those boundaries keep ``coordination.module``
as the paired-ablation variable.
"""

from __future__ import annotations

from typing import Any, Optional

from noema.coordination.base import Advice, CoordinationModule, GenerationContext, Outcome
from noema.coordination.reevo.prompts import (
    SYSTEM_REFLECTOR,
    reflection_code,
    render_short_term_reflection_prompt,
)
from noema.evolution.views import ProgramView

DONOR_REPOSITORY = "ai4co/reevo"
DONOR_COMMIT = "6dce18257da5e11db2d138e417a2fffc5c72d05f"
REFLECTION_TAG = "reevo.short_term_reflection"


def select_better_exemplar(ctx: GenerationContext) -> Optional[ProgramView]:
    """Return the deterministic strictly-fitter local comparison program.

    This is an intentional Noema adaptation: native ReEvo draws two programs
    independently, whereas this coordination-only arm must leave host parent
    selection unchanged.  The ID tie-break avoids consuming module RNG.
    """
    parent = ctx.parent
    if parent is None:
        return None
    eligible = (
        program
        for program in ctx.local_population.top_programs
        if program.id != parent.id and program.fitness > parent.fitness
    )
    return max(eligible, key=lambda program: (program.fitness, program.id), default=None)


class ReEvoShortTermModule(CoordinationModule):
    """One memoryless contrastive reflector call before an eligible mutation."""

    async def advise(self, ctx: GenerationContext) -> Advice:
        parent = ctx.parent
        if parent is None:
            return Advice(attribution={"reevo": {"status": "skipped", "reason": "no_parent"}})

        better = select_better_exemplar(ctx)
        if better is None:
            return Advice(
                attribution={
                    "reevo": {
                        "status": "skipped",
                        "reason": "no_strictly_fitter_local_exemplar",
                        "parent_id": parent.id,
                        "parent_fitness": parent.fitness,
                    }
                }
            )
        if self.llm is None:
            raise RuntimeError("reevo requires the host-provided coordination LLM")

        prompt = render_short_term_reflection_prompt(
            domain_context=str(self.config.get("domain_context", "")),
            function_name=str(
                self.config.get("function_name", self.config.get("func_name", "evolvable"))
            ),
            worse_code=reflection_code(parent.code),
            better_code=reflection_code(better.code),
        )
        reflection = (
            await self.llm.generate_with_context(
                system_message=SYSTEM_REFLECTOR,
                messages=[{"role": "user", "content": prompt}],
                tag=REFLECTION_TAG,
            )
        ).strip()
        metadata: dict[str, Any] = {
            "status": "generated" if reflection else "empty_response",
            "parent_id": parent.id,
            "better_id": better.id,
            "parent_fitness": parent.fitness,
            "better_fitness": better.fitness,
            "local_scope_size": len(ctx.local_population.top_programs),
            "topology": ctx.local_population.topology,
            "donor_repository": DONOR_REPOSITORY,
            "donor_commit": DONOR_COMMIT,
            "reflection": reflection,
        }
        if not reflection:
            return Advice(attribution={"reevo": metadata})
        return Advice(prompt_block=f"[Reflection]\n{reflection}", attribution={"reevo": metadata})

    def report_result(
        self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED
    ) -> None:
        return None

    async def on_generation_end(self, ctx: GenerationContext):
        return None

    def state_dict(self) -> dict[str, Any]:
        return {}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        return None
