"""
Execute phase of the PES arm (LoongFlow: agents/general_agent/executor.py).

Extracted from module.py (task 0060, behavior-identical split). Today the
executor wraps the plan into the standard coordination `Advice` suffix
(advisory mode); the directive mode — the full LoongFlow executor prompt as
the mutation call's primary instruction — lands in task 0065.
"""

from typing import TYPE_CHECKING

from noema.coordination.base import Advice, GenerationContext

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from noema.coordination.pes.module import PESPlannerModule


class Executor:
    """Execute phase: turns a plan into the mutation call's coordination
    input. Shared state lives on the module façade."""

    def __init__(self, module: "PESPlannerModule"):
        self._m = module

    def build_advice(self, plan: str, ctx: GenerationContext) -> Advice:
        """Advisory mode: the plan rides as the standard coordination suffix,
        with the plan + parent id recorded in attribution for report_result."""
        return Advice(
            prompt_block=plan,
            attribution={"plan": plan, "parent_id": ctx.parent.id},
        )

    def retry_block(self, ctx: GenerationContext, error_text: str, attempt: int) -> str:
        """Seed a Stage-1 retry with this lineage's causal reflection (Design 4).

        Returns the stored reflection text (the "why it failed" from the deferred
        summary call) framed as a retry-guidance block, or "" when there's no
        parent or no reflection yet (fresh lineage). The controller concatenates
        this after its raw-error suffix; Null inherits the no-op, so only PES
        retries carry reflection — the controlled variable stays single.
        """
        if ctx.parent is None:
            return ""
        prior = self._m._plans.get(ctx.parent.id)
        reflection = prior.get("reflection") if prior else None
        if not reflection:
            return ""
        return (
            "\n# Reflection on the lineage's last failure\n"
            f"{reflection}\n"
            "Use this causal explanation to guide the corrected mutation."
        )
