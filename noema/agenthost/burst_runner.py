"""Coordination-blind mutation and admission mechanics (task 0163).

Does not import or call ``CoordinationModule`` hooks — seat owns those.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from openevolve.database import Program

from noema.agenthost.materialize import materialize_child_code
from noema.agenthost.mutation import MutationRequest, mutation_layout, prepare_mutation_dir
from noema.evolution.boundary import enforce_immutable_boundary
from noema.evolution.operators import OPERATOR_MENU, OperatorSpec

if TYPE_CHECKING:
    from noema.agenthost.coordinator_seat import BurstChildOutcome
    from noema.agenthost.session import AgentSession


class BurstRunner:
    """Per-child select / mutate / admit without coordination hooks."""

    def __init__(self, session: AgentSession) -> None:
        self._s = session

    def select_parent(self, hints: Dict[str, Any], operator_hint: Optional[str]) -> Dict[str, Any]:
        s = self._s
        s.substrate.set_tokens_spent(s.ledger.spent())
        s._selection = s.substrate.select(
            target_scope=s._target_scope,
            num_inspirations=s.config.num_inspirations,
            hints=hints,
        )
        s._operator_spec = self._choose_operator(requested=operator_hint)
        s._parent2 = None
        if s._operator_spec.arity == 2:
            inspirations = list(s._selection.inspirations)
            if inspirations:
                s._parent2 = s.mutation_operator_rng.choice(inspirations)
        parent = s._selection.parent
        s._phase = "parented"
        return {
            "parent_id": parent.id,
            "parent_code": parent.code,
            "operator": s._operator_spec.name,
            "target_scope": s._target_scope,
            "inspirations": [
                {"id": item.id, "metrics": dict(item.metrics)}
                for item in s._selection.inspirations
            ],
        }

    async def run_mutation(self, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        from noema.agenthost.coordinator_seat import BurstChildOutcome

        s = self._s
        if s.mutation_backend is None:
            raise RuntimeError(
                "run_mutation requires a mutation_backend "
                "(FakeMutationBackend or CliMutationBackend)"
            )
        if not s._base_prompt:
            raise RuntimeError("run_mutation requires a prompt from get_brief")

        parent = s._selection.parent
        s._mutation_attempts += 1
        if s._mutation_attempts > 1 and s._last_error:
            s._prompt = await s._seat.build_retry_prompt(
                s._last_error, s._mutation_attempts - 1
            )
        else:
            s._prompt = dict(s._base_prompt)

        layout = prepare_mutation_dir(
            mutation_layout(
                s.output_dir,
                s._iteration,
                s._mutation_attempts,
                file_suffix=s.config.file_suffix,
            )
        )
        mutation = s.mutation_backend.run(
            MutationRequest(
                prompt=dict(s._prompt),
                parent_code=parent.code,
                work_dir=layout.work_dir,
                deliverable_path=layout.deliverable_path,
                timeout_s=timeout_s if timeout_s is not None else 120.0,
                retry_brief=s._retry_brief,
                layout=layout,
            )
        )
        if not mutation.ok:
            self._write_attempt_trace(
                outcome="provider_failure",
                error=mutation.error,
                layout=layout,
                backend_trace=mutation.backend_trace,
                response=None,
                candidate=None,
                evaluation=None,
            )
            return {
                "status": "mutation_failed",
                "error": mutation.error,
                "required_call": "run_mutation",
                "mutation": dict(mutation.backend_trace),
            }

        child_code = materialize_child_code(
            mutation.code,
            parent.code,
            parse_mode=s._operator_spec.parse_mode,
            language=s.config.language,
            diff_pattern=s.config.diff_pattern,
        )
        if child_code is None:
            s._last_error = "no parseable code in mutation deliverable"
            s._retry_brief = s._last_error
            self._write_attempt_trace(
                outcome="unparseable_response",
                error=s._last_error,
                layout=layout,
                backend_trace=mutation.backend_trace,
                response=mutation.code,
                candidate=None,
                evaluation=None,
            )
            return {
                "status": "mutation_failed",
                "error": s._last_error,
                "required_call": "run_mutation",
                "mutation": dict(mutation.backend_trace),
            }

        merge_new_imports = bool(s._advice.attribution.get("full_executor_prompt"))
        child_code = enforce_immutable_boundary(
            parent.code,
            child_code,
            merge_new_imports=merge_new_imports,
        )
        if child_code is None:
            s._last_error = (
                "mutation broke the EVOLVE-BLOCK boundary: only code inside "
                "EVOLVE-BLOCK-START/END may change (F_imm is immutable)"
            )
            s._retry_brief = s._last_error
            self._write_attempt_trace(
                outcome="immutable_boundary_violation",
                error=s._last_error,
                layout=layout,
                backend_trace=mutation.backend_trace,
                response=mutation.code,
                candidate=None,
                evaluation=None,
            )
            return {
                "status": "mutation_failed",
                "error": s._last_error,
                "required_call": "run_mutation",
                "mutation": dict(mutation.backend_trace),
            }

        outcome = await self.submit_child(code=child_code)
        result = await s._seat.finalize_child(outcome)
        result = {**result, "mutation": dict(mutation.backend_trace)}
        if outcome.status == "rejected":
            s._last_error = outcome.error
            s._retry_brief = outcome.retry_brief
            self._write_attempt_trace(
                outcome="evaluation_failure",
                error=s._last_error,
                layout=layout,
                backend_trace=mutation.backend_trace,
                response=mutation.code,
                candidate={"id": f"it{s._iteration:06d}", "code": child_code},
                evaluation={"error": s._last_error},
            )
        else:
            s._last_error = None
            s._retry_brief = None
        return result

    async def submit_child(self, code: str) -> BurstChildOutcome:
        from noema.coordination import Outcome
        from noema.agenthost.coordinator_seat import BurstChildOutcome

        s = self._s
        parent = s._selection.parent
        child_id = f"it{s._iteration:06d}"
        metrics = await s.evaluator.evaluate_program(code, child_id)
        artifacts = s.evaluator.get_pending_artifacts(child_id)

        if (not metrics) or ("error" in metrics):
            return await self._reject(parent, metrics, artifacts)

        child = Program(
            id=child_id,
            code=code,
            language=s.config.language,
            parent_id=parent.id,
            generation=parent.generation + 1,
            metrics=metrics,
            iteration_found=s._iteration,
            metadata={
                "parent_metrics": parent.metrics,
                "coordination": s._advice.attribution,
                "island": s._target_scope,
            },
        )
        s.substrate.on_child_accepted(
            parent=parent,
            child=child,
            step_size=min(1.0, (s._iteration + 1) / max(1, s.stop_children)),
        )
        s.store.add(child, iteration=s._iteration, target_scope=s._target_scope)
        if artifacts:
            s.store.store_artifacts(child.id, artifacts)
        s.children_accepted += 1
        s._iteration += 1
        s._phase = "complete" if s.children_accepted >= s.stop_children else "open"
        return BurstChildOutcome(
            status="accepted",
            context=s._context,
            advice_attribution=s._advice.attribution,
            child=s.store.view(child),
            outcome=Outcome.ACCEPTED,
            error=None,
            retry_brief=None,
            metrics=dict(metrics),
            program_id=child.id,
            parent_id=parent.id,
            rejections_for_target=None,
        )

    async def _reject(self, parent, metrics, artifacts) -> BurstChildOutcome:
        from noema.coordination import Outcome
        from noema.agenthost.coordinator_seat import BurstChildOutcome

        s = self._s
        s._rejections += 1
        error_text = (metrics or {}).get("error") or (artifacts or {}).get(
            "stderr", "evaluation failed: unknown error"
        )
        s.substrate.on_child_rejected(parent=parent, child=None, eval_failed=True)
        return BurstChildOutcome(
            status="rejected",
            context=s._context,
            advice_attribution=s._advice.attribution,
            child=None,
            outcome=Outcome.EVAL_ERROR,
            error=error_text,
            retry_brief=None,
            metrics=None,
            program_id=None,
            parent_id=None,
            rejections_for_target=s._rejections,
        )

    async def apply_intervention(self, intervention) -> None:
        s = self._s
        for i, proposal in enumerate(intervention.proposals):
            child_id = f"gen{s.generation:04d}-pe{i:03d}"
            metrics = await s.evaluator.evaluate_program(proposal.code, child_id)
            if (not metrics) or ("error" in metrics):
                continue
            child = Program(
                id=child_id,
                code=proposal.code,
                language=s.config.language,
                parent_id=proposal.parent_id,
                generation=s.generation,
                metrics=metrics,
                iteration_found=s._iteration,
                metadata={
                    "origin": proposal.origin,
                    "coordination_proposed": True,
                },
            )
            s.store.add(child, iteration=s._iteration)

    def update_histories(self) -> None:
        s = self._s
        best = s.store.best_program()
        s.best_fitness_history.append(s.store.fitness(best) if best else 0.0)
        all_fitnesses = s.store.all_fitnesses()
        s.avg_fitness_history.append(
            sum(all_fitnesses) / len(all_fitnesses) if all_fitnesses else 0.0
        )
        top = s.store.top_programs(10)
        s.diversity_history.append(
            len(set(program.code for program in top)) / len(top) if top else 0.0
        )

    def _choose_operator(self, requested: Optional[str] = None) -> OperatorSpec:
        s = self._s
        if s.config.mutation_operators is None:
            s._last_operator_trace = {
                "requested": requested,
                "honored": "legacy",
                "ignored": requested,
            }
            return OperatorSpec(
                name="legacy",
                template_key=(
                    "diff_user" if s.config.diff_based_evolution else "full_rewrite_user"
                ),
                parse_mode="diff" if s.config.diff_based_evolution else "full_rewrite",
                arity=1,
                has_thought=False,
            )
        if requested is not None and requested in s.config.mutation_operators:
            s._last_operator_trace = {
                "requested": requested,
                "honored": requested,
                "ignored": None,
            }
            return OPERATOR_MENU[requested]
        name = s.mutation_operator_rng.choice(s.config.mutation_operators)
        s._last_operator_trace = {
            "requested": requested,
            "honored": name,
            "ignored": requested,
        }
        return OPERATOR_MENU[name]

    def _write_attempt_trace(
        self,
        *,
        outcome: str,
        error: Optional[str],
        layout,
        backend_trace: Dict[str, Any],
        response: Optional[str],
        candidate: Optional[Dict[str, Any]],
        evaluation: Optional[Dict[str, Any]],
    ) -> None:
        s = self._s
        advice = s._advice
        attribution = advice.attribution if advice is not None else {}
        parent = s._selection.parent if s._selection is not None else None
        inspirations = (
            list(s._selection.inspirations) if s._selection is not None else []
        )
        s.attempt_tracer.write(
            iteration=s._iteration,
            attempt=s._mutation_attempts,
            outcome=outcome,
            mode="agent_session",
            generation=s.generation,
            arm=s.config.coordination.module,
            substrate=s.config.substrate.kind,
            seed=s.config.random_seed,
            target_scope=s._target_scope,
            source_scope=s._selection.source_scope if s._selection else None,
            parent=(
                {
                    "id": parent.id,
                    "code": parent.code,
                    "metrics": parent.metrics,
                }
                if parent is not None
                else None
            ),
            inspirations=[{"id": item.id} for item in inspirations],
            operator={
                "name": s._operator_spec.name if s._operator_spec else None,
                **s._last_operator_trace,
            },
            coordination={
                "system_block": advice.system_block if advice is not None else None,
                "prompt_block": advice.prompt_block if advice is not None else None,
                "attribution": attribution,
                "mode": (
                    "directive"
                    if attribution.get("full_executor_prompt")
                    else "injected"
                    if advice is not None
                    and (advice.system_block or advice.prompt_block)
                    else "none"
                ),
            },
            prompt=dict(s._prompt) if s._prompt else None,
            response=response,
            candidate=candidate,
            evaluation=evaluation,
            error=error,
            mutation={
                "work_dir": str(layout.work_dir),
                "deliverable": str(layout.deliverable_path),
                "stdout_log": str(layout.stdout_log),
                "stderr_log": str(layout.stderr_log),
                **dict(backend_trace or {}),
            },
            ledger_call_ids=[],
        )
