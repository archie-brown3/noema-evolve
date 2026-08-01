"""Shared per-iteration evolution loop (task 0171).

Extracted from ``NoemaController`` so the controller and agent host driver
share identical selection → advise → mutate → evaluate → admit semantics.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import asdict
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from openevolve.database import Program  # type: ignore[import-untyped]
from openevolve.utils.code_utils import (  # type: ignore[import-untyped]
    extract_diffs,
    format_diff_summary,
    parse_full_rewrite,
)
from openevolve.utils.metrics_utils import get_fitness_score  # type: ignore[import-untyped]

from noema.budget.ledger import BudgetExhausted
from noema.coordination import GenerationContext, Outcome, SelectionContext
from noema.coordination.escalation import EscalationContext
from noema.evolution.boundary import enforce_immutable_boundary
from noema.evolution.diff import apply_diff_lenient as apply_diff
from noema.evolution.operators import OPERATOR_MENU, OperatorSpec
from noema.evolution.prompts import build_mutation_prompt, inject_advice

if TYPE_CHECKING:
    from noema.agenthost.mutation_transport import MutationTransport

logger = logging.getLogger(__name__)


@runtime_checkable
class IterationHost(Protocol):
    """State and services required by :class:`IterationRunner`."""

    config: Any
    output_dir: str
    ledger: Any
    substrate: Any
    population_store: Any
    evaluator: Any
    coordination: Any
    sampler: Any
    mutation_operator_rng: Any
    mutation_transport: MutationTransport
    attempt_tracer: Any
    generation: int
    best_fitness_history: List[float]
    avg_fitness_history: List[float]
    diversity_history: List[float]
    run_iteration_limit: int
    _last_operator_trace: Dict[str, Any]
    _current_advice_call_ids: List[str]
    generation_log: List[Dict[str, Any]]
    escalation: Any


def _log_evolution_trace(host: IterationHost, **kwargs) -> None:
    tracer = getattr(host, "evolution_tracer", None)
    if tracer is not None:
        tracer.log_trace(**kwargs)


class IterationRunner:
    """Per-iteration and generation-tick mechanics shared across hosts."""

    @staticmethod
    def choose_operator(host, requested: Optional[str] = None) -> OperatorSpec:
        """Choose this iteration's mutation operator once (reused across every
        retry attempt, never redrawn per attempt). None config = legacy path,
        byte-identical to today's diff_based_evolution toggle (task 0027).

        `requested` is a coordination pre-selection hint (the bandit arm, task
        0073). It is honored only when the menu is on and the name is one of the
        configured operators; otherwise it is ignored and the incumbent RNG draw
        runs. **When `requested is None` the RNG draw is byte-identical to before
        this parameter existed** — that is the invariant that keeps every other
        arm unchanged. A honored request draws NO operator RNG (only the bandit
        takes this path). The requested/honored/ignored trace is recorded for the
        run log beside the substrate's own selection trace."""
        if host.config.mutation_operators is None:
            host._last_operator_trace = {
                "requested": requested,
                "honored": "legacy",
                "ignored": requested,  # menu off: a request cannot be honored
            }
            return OperatorSpec(
                name="legacy",
                template_key=(
                    "diff_user" if host.config.diff_based_evolution else "full_rewrite_user"
                ),
                parse_mode="diff" if host.config.diff_based_evolution else "full_rewrite",
                arity=1,
                has_thought=False,
            )
        if requested is not None and requested in host.config.mutation_operators:
            host._last_operator_trace = {
                "requested": requested,
                "honored": requested,
                "ignored": None,
            }
            return OPERATOR_MENU[requested]
        name = host.mutation_operator_rng.choice(host.config.mutation_operators)
        host._last_operator_trace = {
            "requested": requested,
            "honored": name,
            "ignored": requested,  # None when there was no request
        }
        return OPERATOR_MENU[name]

    @staticmethod
    async def run_iteration(host, iteration: int) -> None:
        island = host.substrate.target_scope(iteration)
        selection_ctx = SelectionContext(
            iteration=iteration,
            generation=host.generation,
            scope_id=island,
            local_population=host.population_store.snapshot(
                island, limit=host.config.num_top_programs
            ),
            global_population=host.population_store.snapshot(
                None, limit=host.config.num_top_programs
            ),
        )
        request = host.coordination.sampling_request(selection_ctx)
        # Partition the pre-selection request (task 0073): the "operator" key
        # steers the mutation operator and must NOT reach the parent-selection
        # policy (else it would log as an ignored selection hint); every other
        # key is a selection-policy hint owned by the runtime.
        operator_hint = request.hints.get("operator")
        policy_hints = {k: v for k, v in request.hints.items() if k != "operator"}
        host.substrate.set_tokens_spent(host.ledger.spent())
        selection = host.substrate.select(
            target_scope=island,
            num_inspirations=host.config.num_inspirations,
            hints=policy_hints,
        )
        if hasattr(host, "_selection"):
            host._selection = selection
        if hasattr(host, "_target_scope"):
            host._target_scope = island
        parent = selection.parent
        inspirations = list(selection.inspirations)
        parent_island = selection.source_scope

        operator = IterationRunner.choose_operator(host, requested=operator_hint)
        parent2: Optional[Program] = None
        if operator.arity == 2:
            if inspirations:
                parent2 = host.mutation_operator_rng.choice(inspirations)
            else:
                # Early iterations before an island fills up: no second parent
                # available yet. Fall back to arity-1 behavior rather than crash.
                logger.debug(
                    f"Iteration {iteration}: operator {operator.name} wants a second "
                    "parent but inspirations is empty; falling back to arity-1"
                )
        if hasattr(host, "_operator_spec"):
            host._operator_spec = operator
        if hasattr(host, "_parent2"):
            host._parent2 = parent2

        top_programs = host.population_store.top_programs(
            host.config.num_top_programs, scope=parent_island
        )
        previous_programs = host.population_store.top_programs(
            host.config.num_previous_programs, scope=parent_island
        )

        ctx = IterationRunner._make_context(
            host,
            iteration,
            parent_island,
            parent,
            inspirations,
            # Decision #51: the drawn operator's name rides the context so a
            # module can pick operator-specific prompt wording. The legacy
            # no-menu path stays None (there is no EoH operator to name).
            operator=operator.name if host.config.mutation_operators is not None else None,
        )
        # Provenance on ledger records (BudgetedLLM only; injected fakes may not have it).
        # Set this before advice so coordination calls are attributed to this iteration.
        if hasattr(host.mutation_transport, "iteration"):
            host.mutation_transport.iteration = iteration
        if hasattr(host.coordination.llm, "iteration"):
            host.coordination.llm.iteration = iteration
        if hasattr(host.coordination.llm, "generation"):
            host.coordination.llm.generation = host.generation
        advice_ledger_start = len(host.ledger.records)
        try:
            advice = await host.coordination.advise(ctx)  # coordination hook 1
        except Exception as exc:
            host._current_advice_call_ids = [
                record.call_id for record in host.ledger.records[advice_ledger_start:]
            ]
            outcome = "budget_exhausted" if isinstance(exc, BudgetExhausted) else "provider_failure"
            IterationRunner._write_attempt_trace(
                host,
                iteration,
                0,
                ctx,
                selection,
                operator,
                None,
                None,
                None,
                None,
                None,
                outcome,
                repr(exc),
                advice_ledger_start,
            )
            raise
        host._current_advice_call_ids = [
            record.call_id for record in host.ledger.records[advice_ledger_start:]
        ]
        if hasattr(host, "_advice"):
            host._advice = advice
        if hasattr(host, "_context"):
            host._context = ctx

        # Model escalation (task 0107, "B"): layer the controller-owned policy on
        # the arm's Advice. Built from state the host already holds; sets
        # advice.model, which the mutation call below forwards to BudgetedLLM.
        # No-op when escalation is off, so every arm is byte-identical then.
        if host.escalation is not None:
            advice.model = host.escalation.step(
                IterationRunner._build_escalation_context(host, iteration)
            )

        if advice.attribution.get("full_executor_prompt"):
            # Directive-mode fidelity anchor (task 0065, Decision #25 scoped
            # exemption): the advice IS the full prompt — the plan is the
            # mutation call's primary instruction, not a suffix appended to
            # openevolve's own template. Skip build_mutation_prompt/inject_advice
            # entirely and force full-rewrite parsing (the template asks for a
            # full ```python``` block, never a SEARCH/REPLACE diff).
            base_prompt = {"system": advice.system_block, "user": advice.prompt_block}
            prompt = base_prompt
            operator = dataclass_replace(operator, parse_mode="full_rewrite")
        else:
            base_prompt = build_mutation_prompt(
                host.sampler,
                parent=parent,
                top_programs=top_programs,
                previous_programs=previous_programs,
                inspirations=inspirations,
                language=host.config.language,
                iteration=iteration,
                diff_based_evolution=host.config.diff_based_evolution,
                feature_dimensions=host.population_store.feature_dimensions,
                template_key=operator.template_key,
                parent2=parent2,
                metric_fields=host.config.prompt_metric_fields,
            )
            prompt = inject_advice(base_prompt, advice.prompt_block, advice.system_block)

        # BudgetExhausted propagates to run() and stops the loop cleanly.
        # Retry loop: parse/eval failures feed their real error back to
        # the mutation LLM and retry before the iteration counts as spent.
        child_id = f"it{iteration:06d}"
        child_code = None
        changes_summary = None
        metrics = None
        artifacts = None
        eval_failed = True
        error_text = None
        # Which failure category the LAST attempt hit, if the iteration produces
        # no accepted child (task 0090). Defaults to NO_PROGRAM; the eval-error
        # branch upgrades it. Only read when child_code is None below.
        failure_outcome = Outcome.NO_PROGRAM
        retry_cap = host.config.retry_cap if host.config.retry_enabled else 0
        # Best valid attempt across rounds (retry_on="non_improvement" only)
        best_attempt: Optional[Dict[str, Any]] = None
        accepted_trace: Optional[Dict[str, Any]] = None
        source_attempt_id: Optional[str] = None
        parent_fitness = ctx.parent.fitness if ctx.parent is not None else 0.0

        for attempt in range(retry_cap + 1):
            # Reset per attempt so the LAST attempt's failure category is the one
            # reported (task 0090); the eval-error branch below upgrades it.
            failure_outcome = Outcome.NO_PROGRAM
            ledger_start = len(host.ledger.records)
            current_prompt = prompt
            try:
                if attempt > 0:
                    current_prompt = await IterationRunner._build_retry_prompt(
                        host, base_prompt, advice, error_text, attempt, ctx
                    )
                response = await host.mutation_transport.generate_with_context(
                    system_message=current_prompt["system"],
                    messages=[{"role": "user", "content": current_prompt["user"]}],
                    model=advice.model,  # task 0107: None = unchanged (default mutation model)
                )
            except BudgetExhausted as exc:
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    None,
                    None,
                    None,
                    "budget_exhausted",
                    str(exc),
                    ledger_start,
                )
                raise
            except Exception as exc:
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    None,
                    None,
                    None,
                    "provider_failure",
                    repr(exc),
                    ledger_start,
                )
                raise

            materialized = getattr(host, "_materialized_child", None)
            if materialized is not None:
                child_code, changes_summary = materialized
                host._materialized_child = None
            else:
                child_code, changes_summary = IterationRunner._parse_response(
                    host, response, parent.code, operator
                )
            if child_code is None:
                error_text = "no parseable code block found in the response"
                logger.warning(
                    f"Iteration {iteration}: no valid program in LLM response "
                    f"(attempt {attempt + 1})"
                )
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    response,
                    None,
                    None,
                    "unparseable_response",
                    error_text,
                    ledger_start,
                )
                continue

            child_code = enforce_immutable_boundary(
                parent.code,
                child_code,
                # PES-faithful directive mode (task 0065): the child is a full
                # rewrite, so its preamble may need an import F_imm never had.
                # `advice` is fixed for the whole iteration, so every retry
                # attempt gets the same treatment.
                merge_new_imports=bool(advice.attribution.get("full_executor_prompt")),
            )
            if child_code is None:
                error_text = (
                    "mutation broke the EVOLVE-BLOCK boundary: only code inside "
                    "EVOLVE-BLOCK-START/END may change (F_imm is immutable)"
                )
                changes_summary = None
                logger.warning(
                    f"Iteration {iteration}: mutation touched F_imm outside the evolve "
                    f"block (attempt {attempt + 1})"
                )
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    response,
                    None,
                    None,
                    "immutable_boundary_violation",
                    error_text,
                    ledger_start,
                )
                continue

            if len(child_code) > host.config.max_code_length:
                child_length = len(child_code)
                error_text = (
                    f"generated code length {child_length} exceeds max "
                    f"{host.config.max_code_length}"
                )
                child_code = None
                changes_summary = None
                logger.warning(
                    f"Iteration {iteration}: generated code exceeds max length "
                    f"(attempt {attempt + 1}, {child_length} > "
                    f"{host.config.max_code_length})"
                )
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    response,
                    {"id": child_id, "code_length": child_length},
                    None,
                    "over_length",
                    error_text,
                    ledger_start,
                )
                continue

            try:
                metrics = await host.evaluator.evaluate_program(child_code, child_id)
                artifacts = host.evaluator.get_pending_artifacts(child_id)
            except Exception as exc:
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    response,
                    {"id": child_id, "code": child_code},
                    None,
                    "evaluation_failure",
                    repr(exc),
                    ledger_start,
                )
                raise
            # "error" is a RESERVED key in the evaluator metrics contract: an
            # evaluator signals failure by returning {"error": ...} (openevolve
            # convention). A benchmark must not name a genuine score metric
            # "error", or it would be misread as a failed evaluation (task 0056
            # item 4 — documented rather than narrowed, since the convention is
            # what every evaluator already relies on).
            eval_failed = (not metrics) or ("error" in metrics)
            if eval_failed:
                # Applyable code that failed AT evaluation — distinct from a
                # non-program failure for outcome-driven credit assignment (0090).
                failure_outcome = Outcome.EVAL_ERROR
                error_text = (artifacts or {}).get("stderr", "evaluation failed: unknown error")
                logger.warning(
                    f"Iteration {iteration}: evaluation failed "
                    f"(attempt {attempt + 1}): {error_text[:200]}"
                )
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    attempt,
                    ctx,
                    selection,
                    operator,
                    advice,
                    current_prompt,
                    response,
                    {"id": child_id, "code": child_code},
                    {"metrics": metrics, "artifacts": artifacts},
                    "evaluation_failure",
                    error_text,
                    ledger_start,
                )
                child_code = None
                changes_summary = None
                continue

            if host.config.retry_on == "non_improvement" and retry_cap > 0:
                child_fitness = get_fitness_score(metrics, host.population_store.feature_dimensions)
                # Keep the best valid attempt: LoongFlow stores its best
                # candidate even when no round beats the parent
                # (execute_agent_chat.py round semantics), so noema stores the
                # best attempt as the iteration's child either way — population
                # dynamics stay comparable across retry_on modes.
                if best_attempt is None or child_fitness > best_attempt["fitness"]:
                    best_attempt = {
                        "child_code": child_code,
                        "changes_summary": changes_summary,
                        "metrics": metrics,
                        "artifacts": artifacts,
                        "response": response,
                        "prompt": current_prompt,
                        "fitness": child_fitness,
                        "attempt": attempt,
                    }
                if child_fitness <= parent_fitness and attempt < retry_cap:
                    error_text = (
                        "the program evaluated successfully but did not beat its "
                        f"parent: fitness {child_fitness:.4f} <= {parent_fitness:.4f}"
                    )
                    logger.info(
                        f"Iteration {iteration}: valid child without improvement "
                        f"(attempt {attempt + 1}); retrying"
                    )
                    IterationRunner._write_attempt_trace(
                        host,
                        iteration,
                        attempt,
                        ctx,
                        selection,
                        operator,
                        advice,
                        current_prompt,
                        response,
                        {"id": child_id, "code": child_code},
                        {"metrics": metrics, "artifacts": artifacts},
                        "non_improvement",
                        error_text,
                        ledger_start,
                    )
                    continue
                selected = best_attempt is not None and best_attempt["attempt"] == attempt
                if selected and child_fitness > parent_fitness:
                    accepted_trace = {
                        "attempt": attempt,
                        "prompt": current_prompt,
                        "response": response,
                        "candidate": {"id": child_id, "code": child_code},
                        "evaluation": {"metrics": metrics, "artifacts": artifacts},
                        "ledger_start": ledger_start,
                    }
                else:
                    IterationRunner._write_attempt_trace(
                        host,
                        iteration,
                        attempt,
                        ctx,
                        selection,
                        operator,
                        advice,
                        current_prompt,
                        response,
                        {"id": child_id, "code": child_code},
                        {"metrics": metrics, "artifacts": artifacts},
                        "non_improvement",
                        None,
                        ledger_start,
                    )
            else:
                accepted_trace = {
                    "attempt": attempt,
                    "prompt": current_prompt,
                    "response": response,
                    "candidate": {"id": child_id, "code": child_code},
                    "evaluation": {"metrics": metrics, "artifacts": artifacts},
                    "ledger_start": ledger_start,
                }
            break  # success — child_code/metrics/artifacts/changes_summary/current_prompt are set

        if best_attempt is not None:
            # retry_on="non_improvement": store the best valid attempt seen,
            # with its own prompt/response provenance (it may or may not have
            # beaten the parent).
            child_code = best_attempt["child_code"]
            changes_summary = best_attempt["changes_summary"]
            metrics = best_attempt["metrics"]
            artifacts = best_attempt["artifacts"]
            response = best_attempt["response"]
            current_prompt = best_attempt["prompt"]
            source_attempt_id = host.attempt_tracer.attempt_id(iteration, best_attempt["attempt"])
        elif child_code is not None:
            source_attempt_id = host.attempt_tracer.attempt_id(iteration, attempt)

        # Keep optional budget-aware selection policies checkpoint-exact even
        # when the final attempt is rejected or no subsequent selection occurs.
        host.substrate.set_tokens_spent(host.ledger.spent())

        if child_code is None:
            host.substrate.on_child_rejected(parent=parent, child=None, eval_failed=True)
            IterationRunner._record_escalation_signal(host, valid=False, score=None)
            host.coordination.report_result(
                ctx,
                child=None,
                attribution=advice.attribution,
                eval_failed=True,
                outcome=failure_outcome,
            )
            return

        template_key = operator.template_key  # provenance: reuse the draw, don't re-derive
        child = Program(
            id=child_id,
            code=child_code,
            language=host.config.language,
            parent_id=parent.id,
            generation=parent.generation + 1,
            metrics=metrics,
            iteration_found=iteration,
            # Decision #54 (hifo F1 class): the change summary is the program's
            # one-sentence description — hifo's extraction prefers it over the
            # truncated-code fallback, and openevolve's evolution-history
            # rendering picks it up identically for every arm.
            changes_description=changes_summary or "",
            metadata={
                "changes": changes_summary,
                "parent_metrics": parent.metrics,
                "coordination": advice.attribution,
                "island": island,
                "stderr": (artifacts or {}).get("stderr", ""),
                "operator": operator.name,
                "source_attempt_id": source_attempt_id,
            },
            prompts=(
                {
                    template_key: {
                        "system": current_prompt["system"],
                        "user": current_prompt["user"],
                        "responses": [response],
                    }
                }
                if host.config.database.log_prompts
                else None
            ),
        )
        retained_program_id = None
        try:
            host.substrate.on_child_accepted(
                parent=parent,
                child=child,
                step_size=min(1.0, (iteration + 1) / max(1, host.config.max_iterations)),
            )
            retained_program_id = host.population_store.add(
                child, iteration=iteration, target_scope=island
            )
        except Exception as exc:
            if accepted_trace is not None:
                IterationRunner._write_attempt_trace(
                    host,
                    iteration,
                    accepted_trace["attempt"],
                    ctx,
                    selection,
                    operator,
                    advice,
                    accepted_trace["prompt"],
                    accepted_trace["response"],
                    accepted_trace["candidate"],
                    accepted_trace["evaluation"],
                    "acceptance_failure",
                    repr(exc),
                    accepted_trace["ledger_start"],
                )
            host.attempt_tracer.write_selection(
                iteration=iteration,
                program_id=child.id,
                selected_attempt_id=source_attempt_id,
                status="failed",
                error=repr(exc),
            )
            raise

        if accepted_trace is not None:
            IterationRunner._write_attempt_trace(
                host,
                iteration,
                accepted_trace["attempt"],
                ctx,
                selection,
                operator,
                advice,
                accepted_trace["prompt"],
                accepted_trace["response"],
                accepted_trace["candidate"],
                accepted_trace["evaluation"],
                "accepted",
                None,
                accepted_trace["ledger_start"],
            )
        host.attempt_tracer.write_selection(
            iteration=iteration,
            program_id=child.id,
            selected_attempt_id=source_attempt_id,
            status="accepted",
        )
        if artifacts and retained_program_id is not None:
            host.population_store.store_artifacts(child_id, artifacts)

        hook = getattr(host, "on_mutation_child_accepted", None)
        if hook is not None:
            hook(child)

        IterationRunner._record_escalation_signal(
            host,
            valid=True,
            score=get_fitness_score(metrics, host.population_store.feature_dimensions),
        )
        host.coordination.report_result(  # coordination hook 2
            ctx,
            child=host.population_store.view(child),
            attribution=advice.attribution,
            eval_failed=False,
            outcome=Outcome.ACCEPTED,
        )
        _log_evolution_trace(
            host,
            iteration=iteration,
            parent_program=parent,
            child_program=child,
            prompt=current_prompt,
            llm_response=response,
            artifacts=artifacts,
            island_id=island,
            metadata={
                "changes": changes_summary,
                "operator": operator.name,
                "token_ledger": IterationRunner._iteration_ledger_metadata(host, iteration),
            },
        )

    @staticmethod
    def _write_attempt_trace(
        host,
        iteration: int,
        attempt: int,
        ctx: GenerationContext,
        selection,
        operator: OperatorSpec,
        advice: Optional[Any],
        prompt: Optional[Dict[str, str]],
        response: Optional[str],
        candidate: Optional[Dict[str, Any]],
        evaluation: Optional[Dict[str, Any]],
        outcome: str,
        error: Optional[str],
        ledger_start: int,
        ledger_end: Optional[int] = None,
    ) -> None:
        records = host.ledger.records[ledger_start:ledger_end]
        attribution = advice.attribution if advice is not None else {}
        mode = (
            "directive"
            if attribution.get("full_executor_prompt")
            else (
                "injected"
                if advice is not None and (advice.system_block or advice.prompt_block)
                else "none"
            )
        )
        host.attempt_tracer.write(
            iteration=iteration,
            attempt=attempt,
            outcome=outcome,
            generation=host.generation,
            arm=host.config.coordination.module,
            substrate=host.config.substrate.kind,
            seed=host.config.random_seed,
            target_scope=selection.target_scope,
            source_scope=selection.source_scope,
            parent=IterationRunner._program_trace_snapshot(selection.parent),
            inspirations=[
                IterationRunner._program_trace_snapshot(item) for item in selection.inspirations
            ],
            selection=dict(host.substrate.last_selection_trace),
            operator={"name": operator.name, **host._last_operator_trace},
            coordination={
                "system_block": advice.system_block if advice is not None else None,
                "prompt_block": advice.prompt_block if advice is not None else None,
                "attribution": attribution,
                "mode": mode,
                "ledger_call_ids": host._current_advice_call_ids,
            },
            prompt=prompt,
            response=response,
            candidate=candidate,
            evaluation=evaluation,
            error=error,
            ledger_call_ids=[record.call_id for record in records],
            ledger_calls=[asdict(record) for record in records],
        )

    def _program_trace_snapshot(program: Program) -> Dict[str, Any]:
        return {
            "id": program.id,
            "code": program.code,
            "metrics": program.metrics,
            "generation": program.generation,
            "iteration_found": program.iteration_found,
            "parent_id": program.parent_id,
            "metadata": program.metadata,
        }

    @staticmethod
    def _iteration_ledger_metadata(host, iteration: int) -> Dict[str, Any]:
        records = [r for r in host.ledger.records if r.iteration == iteration]
        spent_by_account: Dict[str, int] = {}
        for record in records:
            spent_by_account[record.account] = (
                spent_by_account.get(record.account, 0) + record.total_tokens
            )
        return {
            "spent_by_account": spent_by_account,
            "spent_total": sum(spent_by_account.values()),
            "calls": [asdict(r) for r in records],
        }

    @staticmethod
    def _build_retry_suffix(error_text: str, attempt: int) -> str:
        return (
            "\n\n# Retry After Failure\n"
            f"Your previous attempt failed. Error: {error_text}\n"
            "Produce a corrected program. Re-output the full code."
        )

    @staticmethod
    async def _build_retry_prompt(
        host, base_prompt, advice, error_text, attempt, ctx
    ) -> Dict[str, str]:
        if advice.attribution.get("full_executor_prompt"):
            # Directive mode: re-format the FULL LoongFlow template with
            # {previous_attempts} populated, not the generic retry suffix.
            # build_retry_prompt is duck-typed (not part of the
            # CoordinationModule ABC — base.py stays untouched); only PES
            # directive mode sets the attribution flag that leads here.
            build_directive_retry = getattr(host.coordination, "build_retry_prompt", None)
            if build_directive_retry is not None:
                directive_prompt = build_directive_retry(
                    ctx, advice.attribution, attempt, error_text
                )
                if directive_prompt is not None:
                    return directive_prompt
        prompt = inject_advice(base_prompt, advice.prompt_block, advice.system_block)
        retry_suffix = IterationRunner._build_retry_suffix(error_text, attempt)
        reflection_suffix = await host.coordination.retry_advice(ctx, error_text, attempt)
        prompt["user"] = prompt["user"] + retry_suffix + reflection_suffix
        return prompt

    @staticmethod
    def _parse_response(
        host, response: str, parent_code: str, operator: OperatorSpec
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract child code + a changes summary from the mutation LLM response.

        When operator.has_thought, a brace-delimited thought (if the model
        produced one) is routed through the returned summary / metadata["changes"]
        field instead of the generic diff-summary/"Full rewrite" placeholder —
        that field already reaches future prompts via openevolve's
        _format_evolution_history, so this reuses an existing channel rather
        than adding new plumbing (task 0027). A response with no {...} at all,
        or has_thought=False (m3), falls back to today's exact behavior.
        """
        thought = None
        if operator.has_thought:
            m = re.search(r"\{(.*?)\}", response, re.DOTALL)
            thought = m.group(1).strip() if m else None

        if operator.parse_mode == "diff":
            diff_blocks = extract_diffs(response, host.config.diff_pattern)
            if not diff_blocks:
                return None, None
            child_code = apply_diff(parent_code, response, host.config.diff_pattern)
            return child_code, thought or format_diff_summary(diff_blocks)

        new_code = parse_full_rewrite(response, host.config.language)
        if not new_code:
            return None, None
        return new_code, thought or "Full rewrite"

    @staticmethod
    async def generation_tick(host, iteration: int) -> None:
        host.generation += 1
        IterationRunner._update_histories(host)
        if hasattr(host.coordination.llm, "generation"):
            host.coordination.llm.generation = host.generation

        # The tick is a global event: the module sees the global top programs
        # and population, not one (possibly still empty) island
        ctx = IterationRunner._make_context(
            host,
            iteration,
            island=host.substrate.target_scope(iteration),
            parent=None,
            inspirations=[],
            global_scope=True,
        )
        intervention = await host.coordination.on_generation_end(ctx)  # coordination hook 3
        if intervention is not None and intervention.proposals:
            await IterationRunner._apply_intervention(host, intervention, iteration)
        host.population_store.end_generation()

        host.generation_log.append(
            {
                "generation": host.generation,
                "iteration": iteration,
                "timestamp": time.time(),
                "best_fitness": host.best_fitness_history[-1],
                "avg_fitness": host.avg_fitness_history[-1],
                "diversity": host.diversity_history[-1],
                "tokens_spent": host.ledger.spent(),
                "coordination": host.coordination.log_snapshot(),
                "selection": host.substrate.log_snapshot(),
                "operator_selection": dict(host._last_operator_trace),
            }
        )

    @staticmethod
    async def _apply_intervention(host, intervention, iteration: int) -> None:
        """Evaluate and insert a coordination module's proposed programs (task 0109).

        The module authored these via its own coordination-account LLM calls
        (already metered); the host evaluates and inserts them through the same
        path as ordinary children, so nothing bypasses metering or the store.
        Evaluation runs the code and costs no tokens, so the equal-token basis is
        unchanged. A proposal that fails evaluation is dropped, not inserted.
        """
        for i, proposal in enumerate(intervention.proposals):
            child_id = f"it{iteration:06d}-pe{i:03d}"
            metrics = await host.evaluator.evaluate_program(proposal.code, child_id)
            artifacts = host.evaluator.get_pending_artifacts(child_id)
            if (not metrics) or ("error" in metrics):
                logger.warning(
                    f"Iteration {iteration}: coordination proposal {child_id} "
                    f"({proposal.origin}) failed evaluation; dropped"
                )
                continue
            child = Program(
                id=child_id,
                code=proposal.code,
                language=host.config.language,
                parent_id=proposal.parent_id,
                generation=host.generation,
                metrics=metrics,
                iteration_found=iteration,
                metadata={
                    "origin": proposal.origin,
                    "coordination_proposed": True,
                },
            )
            retained_program_id = host.population_store.add(child, iteration=iteration)
            if artifacts and retained_program_id is not None:
                host.population_store.store_artifacts(child_id, artifacts)

    @staticmethod
    def _build_escalation_context(host, iteration: int) -> EscalationContext:
        """Snapshot the generic signals the escalation policy thresholds, from
        state the host already maintains (histories, ledger, rolling validity)."""
        recent = host._esc_recent_valid
        invalidity_rate = (recent.count(False) / len(recent)) if recent else 0.0
        return EscalationContext(
            best_fitness_history=tuple(host.best_fitness_history),
            recent_scores=tuple(host._esc_recent_scores),
            invalidity_rate=invalidity_rate,
            tokens_spent=host.ledger.spent(),
            tokens_budget=host.config.budget.total_tokens,
            iteration=iteration,
        )

    @staticmethod
    def _record_escalation_signal(host, valid: bool, score: Optional[float]) -> None:
        """One per-iteration observation feeding the invalidity/diversity triggers."""
        if host.escalation is None:
            return
        host._esc_recent_valid.append(valid)
        if score is not None:
            host._esc_recent_scores.append(score)

    @staticmethod
    def _update_histories(host) -> None:
        best = host.population_store.best_program()
        host.best_fitness_history.append(host.population_store.fitness(best) if best else 0.0)

        all_fitnesses = host.population_store.all_fitnesses()
        host.avg_fitness_history.append(
            sum(all_fitnesses) / len(all_fitnesses) if all_fitnesses else 0.0
        )

        top = host.population_store.top_programs(10)
        if top:
            distinct = len(set(p.code for p in top))
            host.diversity_history.append(distinct / len(top))
        else:
            host.diversity_history.append(0.0)

    @staticmethod
    def _make_context(
        host,
        iteration: int,
        island: int,
        parent: Optional[Program],
        inspirations: List[Program],
        global_scope: bool = False,
        operator: Optional[str] = None,
    ) -> GenerationContext:
        local_scope = None if global_scope else island
        # The generation tick is a population-scale event: modules that
        # summarize the population (hifo's top-30% extraction slice, Decision
        # #52 contract) need more than the prompt-sized num_top_programs view.
        # Per-mutation contexts keep the narrow limit so mutation prompts are
        # byte-unchanged.
        limit = None if global_scope else host.config.num_top_programs
        local_population = host.population_store.snapshot(local_scope, limit=limit)
        global_population = host.population_store.snapshot(None, limit=limit)
        return GenerationContext(
            iteration=iteration,
            generation=host.generation,
            scope_id=island,
            parent=host.population_store.view(parent) if parent else None,
            inspirations=host.population_store.views(inspirations),
            local_population=local_population,
            global_population=global_population,
            best_fitness_history=list(host.best_fitness_history),
            avg_fitness_history=list(host.avg_fitness_history),
            diversity_history=list(host.diversity_history),
            operator=operator,
        )
