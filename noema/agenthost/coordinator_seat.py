"""Coordination hook owner for the agent host (task 0163).

Owns ``CoordinationModule`` calls and mutation prompt assembly. ``BurstRunner``
handles mutate/admit without touching coordination.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from typing import Any, Dict, Literal, Optional, TYPE_CHECKING

from noema.coordination import GenerationContext, Outcome, SelectionContext
from noema.evolution.operators import OperatorSpec
from noema.evolution.prompts import build_mutation_prompt, inject_advice

if TYPE_CHECKING:
    from noema.agenthost.burst_runner import BurstRunner
    from noema.agenthost.session import AgentSession


@dataclass
class BurstChildOutcome:
    status: Literal["accepted", "rejected", "mutation_failed"]
    context: Optional[GenerationContext]
    advice_attribution: dict
    child: Any
    outcome: Outcome
    error: Optional[str]
    retry_brief: Optional[str]
    metrics: Optional[dict]
    program_id: Optional[str]
    parent_id: Optional[str]
    rejections_for_target: Optional[int]


class CoordinatorSeat:
    """Coordination hooks and prompt assembly for one agent-host run."""

    def __init__(self, session: AgentSession) -> None:
        self._s = session

    def sampling_hints(self) -> tuple[Dict[str, Any], Optional[str]]:
        s = self._s
        request = s.coordination.sampling_request(
            SelectionContext(
                iteration=s._iteration,
                generation=s.generation,
                scope_id=s._target_scope,
                local_population=s.store.snapshot(
                    s._target_scope, limit=s.config.num_top_programs
                ),
                global_population=s.store.snapshot(
                    None, limit=s.config.num_top_programs
                ),
            )
        )
        operator_hint = request.hints.get("operator")
        hints = {k: v for k, v in request.hints.items() if k != "operator"}
        s._operator_hint = operator_hint
        return hints, operator_hint

    async def plan_brief(self) -> Dict[str, str]:
        s = self._s
        operator = s._operator_spec
        assert operator is not None and s._selection is not None
        s._context = self._make_context(
            operator=operator.name if s.config.mutation_operators is not None else None
        )
        s._advice = await s.coordination.advise(s._context)
        s._base_prompt = self._assemble_mutation_prompt(operator)
        s._prompt = dict(s._base_prompt)
        s._retry_brief = None
        s._last_error = None
        s._phase = "briefed"
        return {
            "prompt": dict(s._prompt),
            "brief": s._prompt["user"],
            "operator": operator.name,
        }

    async def build_retry_prompt(self, error_text: str, attempt: int) -> Dict[str, str]:
        s = self._s
        advice = s._advice
        assert s._base_prompt is not None and s._context is not None
        if advice.attribution.get("full_executor_prompt"):
            build_directive_retry = getattr(s.coordination, "build_retry_prompt", None)
            if build_directive_retry is not None:
                directive_prompt = build_directive_retry(
                    s._context, advice.attribution, attempt, error_text
                )
                if directive_prompt is not None:
                    return directive_prompt
        prompt = inject_advice(
            s._base_prompt, advice.prompt_block, advice.system_block
        )
        retry_suffix = (
            "\n\n# Retry After Failure\n"
            f"Your previous attempt failed. Error: {error_text}\n"
            "Produce a corrected program. Re-output the full code."
        )
        reflection_suffix = await s.coordination.retry_advice(
            s._context, error_text, attempt
        )
        prompt = dict(prompt)
        prompt["user"] = prompt["user"] + retry_suffix + (reflection_suffix or "")
        return prompt

    async def credit_child(self, outcome: BurstChildOutcome) -> None:
        s = self._s
        if outcome.status == "accepted":
            s.coordination.report_result(
                outcome.context,
                child=outcome.child,
                attribution=outcome.advice_attribution,
                eval_failed=False,
                outcome=outcome.outcome,
            )
        elif outcome.status == "rejected":
            s.coordination.report_result(
                outcome.context,
                child=None,
                attribution=outcome.advice_attribution,
                eval_failed=True,
                outcome=outcome.outcome,
            )

    async def finalize_child(self, outcome: BurstChildOutcome) -> Dict[str, Any]:
        """Credit coordination hooks and shape MCP response."""
        s = self._s
        if outcome.status in ("accepted", "rejected"):
            await self.credit_child(outcome)
        if outcome.status == "accepted":
            cadence = s.substrate.steps_per_generation
            if s._iteration % cadence == 0:
                await self.reflect_generation(s._runner)
            return self.outcome_to_dict(outcome)
        if outcome.status == "rejected":
            retry_brief = await s.coordination.retry_advice(
                outcome.context, outcome.error, outcome.rejections_for_target
            )
            result = self.outcome_to_dict(outcome)
            result["retry_brief"] = retry_brief
            return result
        return self.outcome_to_dict(outcome)

    def outcome_to_dict(self, outcome: BurstChildOutcome) -> Dict[str, Any]:
        if outcome.status == "accepted":
            return {
                "status": "accepted",
                "program_id": outcome.program_id,
                "parent_id": outcome.parent_id,
                "metrics": outcome.metrics,
            }
        if outcome.status == "rejected":
            return {
                "status": "rejected",
                "error": outcome.error,
                "retry_brief": outcome.retry_brief,
                "rejections_for_target": outcome.rejections_for_target,
            }
        return {"status": outcome.status, "error": outcome.error}

    async def reflect_generation(self, runner: BurstRunner) -> None:
        s = self._s
        s.generation += 1
        runner.update_histories()
        ctx = dataclass_replace(
            self._make_context(),
            parent=None,
            inspirations=(),
            local_population=s.store.snapshot(None),
            global_population=s.store.snapshot(None),
            generation=s.generation,
            best_fitness_history=list(s.best_fitness_history),
            avg_fitness_history=list(s.avg_fitness_history),
            diversity_history=list(s.diversity_history),
        )
        intervention = await s.coordination.on_generation_end(ctx)
        if intervention is not None and getattr(intervention, "proposals", None):
            await runner.apply_intervention(intervention)
        s.store.end_generation()

    def _assemble_mutation_prompt(self, operator: OperatorSpec) -> Dict[str, str]:
        s = self._s
        advice = s._advice
        parent = s._selection.parent
        inspirations = list(s._selection.inspirations)
        parent_island = s._selection.source_scope
        if advice.attribution.get("full_executor_prompt"):
            return {"system": advice.system_block, "user": advice.prompt_block}
        top_programs = list(
            s.store.top_programs(s.config.num_top_programs, scope=parent_island)
        )
        previous_programs = list(
            s.store.top_programs(
                s.config.num_previous_programs, scope=parent_island
            )
        )
        base_prompt = build_mutation_prompt(
            s.sampler,
            parent=parent,
            top_programs=top_programs,
            previous_programs=previous_programs,
            inspirations=inspirations,
            language=s.config.language,
            iteration=s._iteration,
            diff_based_evolution=s.config.diff_based_evolution,
            feature_dimensions=list(s.store.feature_dimensions),
            template_key=operator.template_key,
            parent2=s._parent2,
            metric_fields=s.config.prompt_metric_fields,
        )
        return inject_advice(base_prompt, advice.prompt_block, advice.system_block)

    def _make_context(self, operator: Optional[str] = None) -> GenerationContext:
        s = self._s
        parent = s._selection.parent if s._selection else None
        inspirations = list(s._selection.inspirations) if s._selection else []
        limit = s.config.num_top_programs
        if operator is None and s._operator_spec is not None:
            operator = (
                s._operator_spec.name
                if s.config.mutation_operators is not None
                else None
            )
        return GenerationContext(
            iteration=s._iteration,
            generation=s.generation,
            scope_id=s._target_scope,
            parent=s.store.view(parent) if parent else None,
            inspirations=s.store.views(inspirations),
            local_population=s.store.snapshot(s._target_scope, limit=limit),
            global_population=s.store.snapshot(None, limit=limit),
            best_fitness_history=list(s.best_fitness_history),
            avg_fitness_history=list(s.avg_fitness_history),
            diversity_history=list(s.diversity_history),
            operator=operator,
        )
