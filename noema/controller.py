"""
The noema controller loop (PLAN.md section 3.3).

Single-process, strictly sequential: sample → advise → prompt → mutate → parse →
evaluate → add → report → generation tick → checkpoint. Coordination state lives
in this process (the released HiFo-Prompt lost its credit-assignment feedback to
joblib subprocess copies — see PLAN.md section 2.2), and the coordination-OFF vs
coordination-ON arms differ ONLY in which CoordinationModule is plugged in.
"""

import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import asdict
from dataclasses import replace as dataclass_replace
from typing import Any, Dict, List, Optional, Tuple

from openevolve.database import Program
from openevolve.evolution_trace import EvolutionTracer
from openevolve.utils.code_utils import (
    extract_diffs,
    format_diff_summary,
    parse_full_rewrite,
)
from openevolve.utils.metrics_utils import get_fitness_score

# Indentation-aware SEARCH/REPLACE application: openevolve's apply_diff requires
# a byte-exact match on the SEARCH block, so an LLM that re-indents the snippet
# silently produces a no-op diff. apply_diff_lenient tolerates that.
from noema.diff import apply_diff_lenient as apply_diff

from noema.budget.ledger import (
    COORDINATION_ACCOUNT,
    MUTATION_ACCOUNT,
    BudgetExhausted,
    TokenLedger,
)
from noema.budget.llm import BudgetedLLM
from noema.config import NoemaConfig
from noema.coordination import (
    CoordinationModule,
    GenerationContext,
    SelectionContext,
    build_coordination_module,
)
from noema.boundary import enforce_immutable_boundary
from noema.registry import build_substrate_runtime
from noema.operators import OPERATOR_MENU, OperatorSpec
from noema.evaluator import make_evaluator
from noema.prompts import build_mutation_prompt, inject_advice, make_prompt_sampler

logger = logging.getLogger(__name__)

NOEMA_STATE_FILE = "noema_state.json"
FROZEN_CONFIG_FILE = "config.yaml"


def _encode_rng_state(state) -> list:
    """random.getstate() -> JSON-serializable (tuples become lists)"""
    return [state[0], list(state[1]), state[2]]


def _decode_rng_state(encoded) -> tuple:
    return (encoded[0], tuple(encoded[1]), encoded[2])


class NoemaController:
    """
    Owns the evolution loop; borrows OpenEvolve's database/evaluator/prompt
    sampler via the substrate adapters and calls the coordination hooks.

    Args:
        config: Experiment configuration.
        evaluation_file: Path to an OpenEvolve-style eval script (defines evaluate()).
        initial_program_code: Seed program source.
        output_dir: Where checkpoints and logs go.
        mutation_llm / coordination / ledger: Injectable for tests; built from
            config when omitted.
    """

    def __init__(
        self,
        config: NoemaConfig,
        evaluation_file: str,
        initial_program_code: str,
        output_dir: str = "noema_output",
        mutation_llm=None,
        coordination: Optional[CoordinationModule] = None,
        ledger: Optional[TokenLedger] = None,
    ):
        self.config = config
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._freeze_config(output_dir, config)

        self.ledger = ledger or TokenLedger(
            total_budget_tokens=config.budget.total_tokens,
            account_caps=config.budget.account_caps,
            log_path=config.budget.log_path or os.path.join(output_dir, "llm_calls.jsonl"),
        )
        self.evolution_tracer = EvolutionTracer(
            output_path=os.path.join(output_dir, "evolution_trace.jsonl"),
            format="jsonl",
            include_code=False,
            include_prompts=True,
            enabled=True,
            buffer_size=1,
        )

        # Substrate (borrowed OpenEvolve components behind adapters).
        # Note: SubstrateDatabase construction seeds the GLOBAL random module
        # from config.database.random_seed (openevolve behavior); we re-seed
        # explicitly below so the policy is visible here.
        self.substrate = build_substrate_runtime(config)
        # Compatibility alias for existing diagnostics and adapter tests. New
        # controller behavior routes selection/lifecycle through self.substrate.
        self.db = self.substrate.store
        self.evaluator = make_evaluator(
            config.evaluator, evaluation_file, suffix=config.file_suffix
        )
        self.sampler = make_prompt_sampler(config.prompt)

        # RNG policy: global `random` drives openevolve's DB sampling;
        # the coordination module gets its own stream so arms with/without
        # coordination consume identical randomness from the shared stream
        random.seed(config.random_seed)
        self.coordination_rng = random.Random(config.coordination.seed)
        # Same isolation pattern: a dedicated stream so turning the EoH
        # operator menu on/off never perturbs any other RNG consumer's draw
        # sequence (task 0027).
        self.mutation_operator_rng = random.Random(config.mutation_operator_seed)

        self.mutation_llm = mutation_llm or BudgetedLLM(
            model=config.llm.model,
            ledger=self.ledger,
            account=MUTATION_ACCOUNT,
            tag="mutate",
            api_base=config.llm.api_base,
            api_key=config.llm.api_key,
            temperature=config.llm.temperature,
            top_p=config.llm.top_p,
            max_tokens=config.llm.max_tokens,
            seed=config.llm.seed,
            timeout=config.llm.timeout,
            retries=config.llm.retries,
            retry_delay=config.llm.retry_delay,
        )

        if coordination is not None:
            self.coordination = coordination
        else:
            coordination_llm = BudgetedLLM(
                model=config.llm.model,
                ledger=self.ledger,
                account=COORDINATION_ACCOUNT,
                tag=f"{config.coordination.module}.coordination",
                api_base=config.llm.api_base,
                api_key=config.llm.api_key,
                temperature=config.llm.temperature,
                top_p=config.llm.top_p,
                max_tokens=config.llm.max_tokens,
                seed=config.llm.seed,
                timeout=config.llm.timeout,
                retries=config.llm.retries,
                retry_delay=config.llm.retry_delay,
            )
            # Domain constraints (e.g. "explicit constructor, not iterative
            # search") are problem context, not search mechanics — safe for a
            # coordination module to see. Modules that don't look for this key
            # ignore it, like any other mechanism-specific coordination param.
            coordination_params = dict(config.coordination.params)
            coordination_params.setdefault("domain_context", config.prompt.system_message)
            # Task 0080 removed the `island_bests_provider` callable that used to
            # be injected here. Cross-region best scores (task 0061) now reach a
            # module through `GenerationContext.global_population.regions` — a
            # neutral snapshot, not a live callback into a concrete store.
            self.coordination = build_coordination_module(
                config.coordination.module,
                coordination_params,
                llm=coordination_llm,
                rng=self.coordination_rng,
            )

        self.initial_program_code = initial_program_code

        # Host-maintained histories, one entry per generation tick. Fixed
        # definitions, identical across arms:
        #   best  = fitness of the global best program
        #   avg   = mean fitness over all programs in the database
        #   diversity = distinct-code fraction among the global top 10
        self.best_fitness_history: List[float] = []
        self.avg_fitness_history: List[float] = []
        self.diversity_history: List[float] = []

        self.generation = 0
        self.start_iteration = 0
        self.generation_log: List[Dict[str, Any]] = []

    @staticmethod
    def _freeze_config(output_dir: str, config: NoemaConfig) -> None:
        """Write the fully-resolved launch config once; a checkpoint resume
        (same output_dir, new NoemaController) must not clobber the original."""
        path = os.path.join(output_dir, FROZEN_CONFIG_FILE)
        if os.path.exists(path):
            return
        text = config.to_yaml()
        with open(path, "w") as f:
            f.write(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        logger.info(f"Froze run config to {path} (sha256={digest})")

    # ------------------------------------------------------------------ run

    async def run(self, iterations: Optional[int] = None) -> Optional[Program]:
        """Run the evolution loop; returns the best program found"""
        try:
            await self._ensure_initial_program()

            max_iterations = iterations if iterations is not None else self.config.max_iterations
            end_iteration = self.start_iteration + max_iterations

            next_iteration = self.start_iteration
            for iteration in range(self.start_iteration, end_iteration):
                try:
                    await self._run_iteration(iteration)
                    next_iteration = iteration + 1

                    # Coordination LLM calls in the generation tick may also
                    # exhaust the (shared) budget — stop cleanly either way
                    if next_iteration % self.substrate.steps_per_generation == 0:
                        await self._generation_tick(iteration)
                except BudgetExhausted as e:
                    logger.info(f"Stopping at iteration {iteration}: {e}")
                    break

                if next_iteration % self.config.checkpoint_interval == 0:
                    self.save_checkpoint(iteration)

            completed_any = next_iteration > self.start_iteration
            self.start_iteration = next_iteration
            if completed_any:
                self.save_checkpoint(next_iteration - 1)
            return self.db.best_program()
        finally:
            self.evolution_tracer.close()

    async def _ensure_initial_program(self) -> None:
        if self.db.num_programs > 0:
            return
        logger.info("Evaluating and adding initial program")
        program_id = "initial"
        metrics = await self.evaluator.evaluate_program(self.initial_program_code, program_id)
        program = Program(
            id=program_id,
            code=self.initial_program_code,
            language=self.config.language,
            metrics=metrics,
            iteration_found=0,
        )
        self.db.add(program, iteration=0)
        artifacts = self.evaluator.get_pending_artifacts(program_id)
        if artifacts:
            self.db.store_artifacts(program_id, artifacts)

    # ------------------------------------------------------------ iteration

    def _choose_operator(self) -> OperatorSpec:
        """Choose this iteration's mutation operator once (reused across every
        retry attempt, never redrawn per attempt). None config = legacy path,
        byte-identical to today's diff_based_evolution toggle (task 0027)."""
        if self.config.mutation_operators is None:
            return OperatorSpec(
                name="legacy",
                template_key=(
                    "diff_user" if self.config.diff_based_evolution else "full_rewrite_user"
                ),
                parse_mode="diff" if self.config.diff_based_evolution else "full_rewrite",
                arity=1,
                has_thought=False,
            )
        name = self.mutation_operator_rng.choice(self.config.mutation_operators)
        return OPERATOR_MENU[name]

    async def _run_iteration(self, iteration: int) -> None:
        island = self.substrate.target_scope(iteration)
        selection_ctx = SelectionContext(
            iteration=iteration,
            generation=self.generation,
            scope_id=island,
            local_population=self.db.snapshot(
                island, limit=self.config.num_top_programs
            ),
            global_population=self.db.snapshot(
                None, limit=self.config.num_top_programs
            ),
        )
        request = self.coordination.sampling_request(selection_ctx)
        self.substrate.set_tokens_spent(self.ledger.spent())
        selection = self.substrate.select(
            target_scope=island,
            num_inspirations=self.config.num_inspirations,
            hints=request.hints,
        )
        parent = selection.parent
        inspirations = list(selection.inspirations)
        parent_island = selection.source_scope

        operator = self._choose_operator()
        parent2: Optional[Program] = None
        if operator.arity == 2:
            if inspirations:
                parent2 = self.mutation_operator_rng.choice(inspirations)
            else:
                # Early iterations before an island fills up: no second parent
                # available yet. Fall back to arity-1 behavior rather than crash.
                logger.debug(
                    f"Iteration {iteration}: operator {operator.name} wants a second "
                    "parent but inspirations is empty; falling back to arity-1"
                )

        top_programs = self.db.top_programs(
            self.config.num_top_programs, scope=parent_island
        )
        previous_programs = self.db.top_programs(
            self.config.num_previous_programs, scope=parent_island
        )

        ctx = self._make_context(iteration, parent_island, parent, inspirations)
        advice = await self.coordination.advise(ctx)  # coordination hook 1

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
                self.sampler,
                parent=parent,
                top_programs=top_programs,
                previous_programs=previous_programs,
                inspirations=inspirations,
                language=self.config.language,
                iteration=iteration,
                diff_based_evolution=self.config.diff_based_evolution,
                feature_dimensions=self.db.feature_dimensions,
                template_key=operator.template_key,
                parent2=parent2,
            )
            prompt = inject_advice(base_prompt, advice.prompt_block, advice.system_block)

        # Provenance on ledger records (BudgetedLLM only; injected fakes may not have it)
        if hasattr(self.mutation_llm, "iteration"):
            self.mutation_llm.iteration = iteration
        if hasattr(self.coordination.llm, "iteration"):
            self.coordination.llm.iteration = iteration

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
        retry_cap = self.config.retry_cap if self.config.retry_enabled else 0
        # Best valid attempt across rounds (retry_on="non_improvement" only)
        best_attempt: Optional[Dict[str, Any]] = None
        parent_fitness = ctx.parent.fitness if ctx.parent is not None else 0.0

        for attempt in range(retry_cap + 1):
            if attempt > 0:
                current_prompt = await self._build_retry_prompt(
                    base_prompt, advice, error_text, attempt, ctx
                )
            else:
                current_prompt = prompt

            response = await self.mutation_llm.generate_with_context(
                system_message=current_prompt["system"],
                messages=[{"role": "user", "content": current_prompt["user"]}],
            )

            child_code, changes_summary = self._parse_response(response, parent.code, operator)
            if child_code is None:
                error_text = "no parseable code block found in the response"
                logger.warning(
                    f"Iteration {iteration}: no valid program in LLM response "
                    f"(attempt {attempt + 1})"
                )
                continue

            child_code = enforce_immutable_boundary(parent.code, child_code)
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
                continue

            if len(child_code) > self.config.max_code_length:
                child_length = len(child_code)
                error_text = (
                    f"generated code length {child_length} exceeds max "
                    f"{self.config.max_code_length}"
                )
                child_code = None
                changes_summary = None
                logger.warning(
                    f"Iteration {iteration}: generated code exceeds max length "
                    f"(attempt {attempt + 1}, {child_length} > "
                    f"{self.config.max_code_length})"
                )
                continue

            metrics = await self.evaluator.evaluate_program(child_code, child_id)
            artifacts = self.evaluator.get_pending_artifacts(child_id)
            # "error" is a RESERVED key in the evaluator metrics contract: an
            # evaluator signals failure by returning {"error": ...} (openevolve
            # convention). A benchmark must not name a genuine score metric
            # "error", or it would be misread as a failed evaluation (task 0056
            # item 4 — documented rather than narrowed, since the convention is
            # what every evaluator already relies on).
            eval_failed = (not metrics) or ("error" in metrics)
            if eval_failed:
                error_text = (artifacts or {}).get("stderr",
                                                     "evaluation failed: unknown error")
                logger.warning(
                    f"Iteration {iteration}: evaluation failed "
                    f"(attempt {attempt + 1}): {error_text[:200]}"
                )
                child_code = None
                changes_summary = None
                continue

            if self.config.retry_on == "non_improvement" and retry_cap > 0:
                child_fitness = get_fitness_score(metrics, self.db.feature_dimensions)
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
                    continue

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

        # Keep optional budget-aware selection policies checkpoint-exact even
        # when the final attempt is rejected or no subsequent selection occurs.
        self.substrate.set_tokens_spent(self.ledger.spent())

        if child_code is None:
            self.substrate.on_child_rejected(
                parent=parent, child=None, eval_failed=True
            )
            self.coordination.report_result(
                ctx, child=None, attribution=advice.attribution, eval_failed=True
            )
            return

        template_key = operator.template_key  # provenance: reuse the draw, don't re-derive
        child = Program(
            id=child_id,
            code=child_code,
            language=self.config.language,
            parent_id=parent.id,
            generation=parent.generation + 1,
            metrics=metrics,
            iteration_found=iteration,
            metadata={
                "changes": changes_summary,
                "parent_metrics": parent.metrics,
                "coordination": advice.attribution,
                "island": island,
                "stderr": (artifacts or {}).get("stderr", ""),
                "operator": operator.name,
            },
            prompts=(
                {
                    template_key: {
                        "system": current_prompt["system"],
                        "user": current_prompt["user"],
                        "responses": [response],
                    }
                }
                if self.config.database.log_prompts
                else None
            ),
        )
        self.substrate.on_child_accepted(
            parent=parent,
            child=child,
            step_size=min(1.0, (iteration + 1) / max(1, self.config.max_iterations)),
        )
        self.db.add(child, iteration=iteration, target_scope=island)
        if artifacts:
            self.db.store_artifacts(child_id, artifacts)

        self.coordination.report_result(  # coordination hook 2
            ctx,
            child=self.db.view(child),
            attribution=advice.attribution,
            eval_failed=False,
        )
        self.evolution_tracer.log_trace(
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
                "token_ledger": self._iteration_ledger_metadata(iteration),
            },
        )

    def _iteration_ledger_metadata(self, iteration: int) -> Dict[str, Any]:
        records = [r for r in self.ledger.records if r.iteration == iteration]
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

    def _build_retry_suffix(self, error_text: str, attempt: int) -> str:
        return (
            "\n\n# Retry After Failure\n"
            f"Your previous attempt failed. Error: {error_text}\n"
            "Produce a corrected program. Re-output the full code."
        )

    async def _build_retry_prompt(
        self, base_prompt, advice, error_text, attempt, ctx
    ) -> Dict[str, str]:
        if advice.attribution.get("full_executor_prompt"):
            # Directive mode: re-format the FULL LoongFlow template with
            # {previous_attempts} populated, not the generic retry suffix.
            # build_retry_prompt is duck-typed (not part of the
            # CoordinationModule ABC — base.py stays untouched); only PES
            # directive mode sets the attribution flag that leads here.
            build_directive_retry = getattr(self.coordination, "build_retry_prompt", None)
            if build_directive_retry is not None:
                directive_prompt = build_directive_retry(ctx, advice.attribution, attempt, error_text)
                if directive_prompt is not None:
                    return directive_prompt
        prompt = inject_advice(base_prompt, advice.prompt_block, advice.system_block)
        retry_suffix = self._build_retry_suffix(error_text, attempt)
        reflection_suffix = await self.coordination.retry_advice(ctx, error_text, attempt)
        prompt["user"] = prompt["user"] + retry_suffix + reflection_suffix
        return prompt

    def _parse_response(
        self, response: str, parent_code: str, operator: OperatorSpec
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
            diff_blocks = extract_diffs(response, self.config.diff_pattern)
            if not diff_blocks:
                return None, None
            child_code = apply_diff(parent_code, response, self.config.diff_pattern)
            return child_code, thought or format_diff_summary(diff_blocks)

        new_code = parse_full_rewrite(response, self.config.language)
        if not new_code:
            return None, None
        return new_code, thought or "Full rewrite"

    # ----------------------------------------------------------- generation

    async def _generation_tick(self, iteration: int) -> None:
        self.generation += 1
        self._update_histories()

        # The tick is a global event: the module sees the global top programs
        # and population, not one (possibly still empty) island
        ctx = self._make_context(
            iteration,
            island=self.substrate.target_scope(iteration),
            parent=None,
            inspirations=[],
            global_scope=True,
        )
        await self.coordination.on_generation_end(ctx)  # coordination hook 3
        self.db.end_generation()

        self.generation_log.append(
            {
                "generation": self.generation,
                "iteration": iteration,
                "timestamp": time.time(),
                "best_fitness": self.best_fitness_history[-1],
                "avg_fitness": self.avg_fitness_history[-1],
                "diversity": self.diversity_history[-1],
                "tokens_spent": self.ledger.spent(),
                "coordination": self.coordination.log_snapshot(),
                "selection": self.substrate.log_snapshot(),
            }
        )

    def _update_histories(self) -> None:
        best = self.db.best_program()
        self.best_fitness_history.append(self.db.fitness(best) if best else 0.0)

        all_fitnesses = self.db.all_fitnesses()
        self.avg_fitness_history.append(
            sum(all_fitnesses) / len(all_fitnesses) if all_fitnesses else 0.0
        )

        top = self.db.top_programs(10)
        if top:
            distinct = len(set(p.code for p in top))
            self.diversity_history.append(distinct / len(top))
        else:
            self.diversity_history.append(0.0)

    def _make_context(
        self,
        iteration: int,
        island: int,
        parent: Optional[Program],
        inspirations: List[Program],
        global_scope: bool = False,
    ) -> GenerationContext:
        local_scope = None if global_scope else island
        local_population = self.db.snapshot(
            local_scope, limit=self.config.num_top_programs
        )
        global_population = self.db.snapshot(
            None, limit=self.config.num_top_programs
        )
        return GenerationContext(
            iteration=iteration,
            generation=self.generation,
            scope_id=island,
            parent=self.db.view(parent) if parent else None,
            inspirations=self.db.views(inspirations),
            local_population=local_population,
            global_population=global_population,
            best_fitness_history=list(self.best_fitness_history),
            avg_fitness_history=list(self.avg_fitness_history),
            diversity_history=list(self.diversity_history),
        )

    # ---------------------------------------------------------- checkpoints

    def save_checkpoint(self, iteration: int) -> str:
        path = os.path.join(self.output_dir, "checkpoints", f"checkpoint_{iteration}")
        os.makedirs(path, exist_ok=True)
        self.db.save(path, iteration)
        self.substrate.set_tokens_spent(self.ledger.spent())
        state = {
            "next_iteration": iteration + 1,
            "generation": self.generation,
            "best_fitness_history": self.best_fitness_history,
            "avg_fitness_history": self.avg_fitness_history,
            "diversity_history": self.diversity_history,
            "generation_log": self.generation_log,
            "ledger": self.ledger.snapshot(),
            "coordination": self.coordination.state_dict(),
            "substrate_runtime": self.substrate.state_dict(),
            "global_rng_state": _encode_rng_state(random.getstate()),
            "coordination_rng_state": _encode_rng_state(self.coordination_rng.getstate()),
            "mutation_operator_rng_state": _encode_rng_state(
                self.mutation_operator_rng.getstate()
            ),
        }
        with open(os.path.join(path, NOEMA_STATE_FILE), "w") as f:
            json.dump(state, f)
        logger.info(f"Saved checkpoint at iteration {iteration} to {path}")
        return path

    def load_checkpoint(self, path: str) -> None:
        self.db.load(path)
        with open(os.path.join(path, NOEMA_STATE_FILE)) as f:
            state = json.load(f)
        self.start_iteration = state["next_iteration"]
        self.generation = state["generation"]
        self.best_fitness_history = state["best_fitness_history"]
        self.avg_fitness_history = state["avg_fitness_history"]
        self.diversity_history = state["diversity_history"]
        self.generation_log = state.get("generation_log", [])
        self.ledger.restore(state["ledger"])
        self.coordination.load_state_dict(state["coordination"])
        self.substrate.load_state_dict(state.get("substrate_runtime", {}))
        random.setstate(_decode_rng_state(state["global_rng_state"]))
        self.coordination_rng.setstate(_decode_rng_state(state["coordination_rng_state"]))
        if "mutation_operator_rng_state" in state:
            self.mutation_operator_rng.setstate(
                _decode_rng_state(state["mutation_operator_rng_state"])
            )
        logger.info(f"Loaded checkpoint from {path} (resuming at {self.start_iteration})")
