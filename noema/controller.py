"""
The noema controller loop (PLAN.md section 3.3).

Single-process, strictly sequential: sample → advise → prompt → mutate → parse →
evaluate → add → report → generation tick → checkpoint. Coordination state lives
in this process (the released HiFo-Prompt lost its credit-assignment feedback to
joblib subprocess copies — see PLAN.md section 2.2), and the coordination-OFF vs
coordination-ON arms differ ONLY in which CoordinationModule is plugged in.
"""

import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from openevolve.database import Program
from openevolve.utils.code_utils import (
    apply_diff,
    extract_diffs,
    format_diff_summary,
    parse_full_rewrite,
)

from noema.budget.ledger import (
    COORDINATION_ACCOUNT,
    MUTATION_ACCOUNT,
    BudgetExhausted,
    TokenLedger,
)
from noema.budget.llm import BudgetedLLM
from noema.config import NoemaConfig
from noema.coordination import CoordinationModule, GenerationContext, build_coordination_module
from noema.substrate.database import SubstrateDatabase
from noema.substrate.evaluator import make_evaluator
from noema.substrate.prompts import build_mutation_prompt, inject_advice, make_prompt_sampler

logger = logging.getLogger(__name__)

NOEMA_STATE_FILE = "noema_state.json"


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

        self.ledger = ledger or TokenLedger(
            total_budget_tokens=config.budget.total_tokens,
            account_caps=config.budget.account_caps,
            log_path=config.budget.log_path or os.path.join(output_dir, "llm_calls.jsonl"),
        )

        # Substrate (borrowed OpenEvolve components behind adapters).
        # Note: SubstrateDatabase construction seeds the GLOBAL random module
        # from config.database.random_seed (openevolve behavior); we re-seed
        # explicitly below so the policy is visible here.
        self.db = SubstrateDatabase(config.database)
        self.evaluator = make_evaluator(
            config.evaluator, evaluation_file, suffix=config.file_suffix
        )
        self.sampler = make_prompt_sampler(config.prompt)

        # RNG policy: global `random` drives openevolve's DB sampling;
        # the coordination module gets its own stream so arms with/without
        # coordination consume identical randomness from the shared stream
        random.seed(config.random_seed)
        self.coordination_rng = random.Random(config.coordination.seed)

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
            # ignore it, same convention as Advice.sampling_hint.
            coordination_params = dict(config.coordination.params)
            coordination_params.setdefault("domain_context", config.prompt.system_message)
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

    # ------------------------------------------------------------------ run

    async def run(self, iterations: Optional[int] = None) -> Optional[Program]:
        """Run the evolution loop; returns the best program found"""
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
                if next_iteration % self.db.num_islands == 0:
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

    async def _run_iteration(self, iteration: int) -> None:
        island = iteration % self.db.num_islands
        parent, inspirations = self.db.sample_from_island(island, self.config.num_inspirations)
        parent_island = parent.metadata.get("island", island)

        top_programs = self.db.top_programs(self.config.num_top_programs, island=parent_island)
        previous_programs = self.db.top_programs(
            self.config.num_previous_programs, island=parent_island
        )

        ctx = self._make_context(iteration, parent_island, parent, inspirations)
        advice = await self.coordination.advise(ctx)  # coordination hook 1

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
        )
        prompt = inject_advice(base_prompt, advice.prompt_block, advice.system_block)

        # Provenance on ledger records (BudgetedLLM only; injected fakes may not have it)
        if hasattr(self.mutation_llm, "iteration"):
            self.mutation_llm.iteration = iteration
        if hasattr(self.coordination.llm, "iteration"):
            self.coordination.llm.iteration = iteration

        # BudgetExhausted propagates to run() and stops the loop cleanly
        response = await self.mutation_llm.generate_with_context(
            system_message=prompt["system"],
            messages=[{"role": "user", "content": prompt["user"]}],
        )

        child_code, changes_summary = self._parse_response(response, parent.code)
        if child_code is None:
            logger.warning(f"Iteration {iteration}: no valid program in LLM response")
            self.coordination.report_result(
                ctx, child=None, attribution=advice.attribution, eval_failed=True
            )
            return
        if len(child_code) > self.config.max_code_length:
            logger.warning(
                f"Iteration {iteration}: generated code exceeds max length "
                f"({len(child_code)} > {self.config.max_code_length})"
            )
            self.coordination.report_result(
                ctx, child=None, attribution=advice.attribution, eval_failed=True
            )
            return

        # Deterministic IDs (one child per iteration): identical across arms, so
        # openevolve's set-based island iteration order — which depends on the
        # id strings — cannot make otherwise-identical runs diverge
        child_id = f"it{iteration:06d}"
        metrics = await self.evaluator.evaluate_program(child_code, child_id)
        artifacts = self.evaluator.get_pending_artifacts(child_id)

        template_key = "diff_user" if self.config.diff_based_evolution else "full_rewrite_user"
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
                # The island this child is placed in (the rotation target for
                # this iteration), not parent_island (which may differ from
                # `island` when sample_from_island fell back cross-island for
                # an empty island — matches openevolve's own process_parallel
                # fix for this exact bug class, upstream issue #391).
                "island": island,
                # The evaluator's error text (empty on success). Rides on
                # metadata so coordination modules see WHY a child failed — the
                # ProgramView they get copies metadata verbatim. Independent of
                # prompt.include_artifacts, which governs the mutation prompt.
                "stderr": (artifacts or {}).get("stderr", ""),
            },
            prompts=(
                {
                    template_key: {
                        "system": prompt["system"],
                        "user": prompt["user"],
                        "responses": [response],
                    }
                }
                if self.config.database.log_prompts
                else None
            ),
        )
        self.db.add(child, iteration=iteration, target_island=island)
        if artifacts:
            self.db.store_artifacts(child_id, artifacts)

        eval_failed = (not metrics) or ("error" in metrics)
        self.coordination.report_result(  # coordination hook 2
            ctx,
            child=self.db.view(child),
            attribution=advice.attribution,
            eval_failed=eval_failed,
        )

    def _parse_response(
        self, response: str, parent_code: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract child code + a changes summary from the mutation LLM response"""
        if self.config.diff_based_evolution:
            diff_blocks = extract_diffs(response, self.config.diff_pattern)
            if not diff_blocks:
                return None, None
            child_code = apply_diff(parent_code, response, self.config.diff_pattern)
            return child_code, format_diff_summary(diff_blocks)
        new_code = parse_full_rewrite(response, self.config.language)
        if not new_code:
            return None, None
        return new_code, "Full rewrite"

    # ----------------------------------------------------------- generation

    async def _generation_tick(self, iteration: int) -> None:
        self.generation += 1
        self._update_histories()

        # The tick is a global event: the module sees the global top programs
        # and population, not one (possibly still empty) island
        ctx = self._make_context(
            iteration,
            island=iteration % self.db.num_islands,
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
        top_island = None if global_scope else island
        fitnesses = self.db.all_fitnesses() if global_scope else self.db.island_fitnesses(island)
        return GenerationContext(
            iteration=iteration,
            generation=self.generation,
            island=island,
            parent=self.db.view(parent) if parent else None,
            inspirations=self.db.views(inspirations),
            top_programs=self.db.views(
                self.db.top_programs(self.config.num_top_programs, island=top_island)
            ),
            island_fitnesses=fitnesses,
            best_fitness_history=list(self.best_fitness_history),
            avg_fitness_history=list(self.avg_fitness_history),
            diversity_history=list(self.diversity_history),
        )

    # ---------------------------------------------------------- checkpoints

    def save_checkpoint(self, iteration: int) -> str:
        path = os.path.join(self.output_dir, "checkpoints", f"checkpoint_{iteration}")
        os.makedirs(path, exist_ok=True)
        self.db.save(path, iteration)
        state = {
            "next_iteration": iteration + 1,
            "generation": self.generation,
            "best_fitness_history": self.best_fitness_history,
            "avg_fitness_history": self.avg_fitness_history,
            "diversity_history": self.diversity_history,
            "generation_log": self.generation_log,
            "ledger": self.ledger.snapshot(),
            "coordination": self.coordination.state_dict(),
            "global_rng_state": _encode_rng_state(random.getstate()),
            "coordination_rng_state": _encode_rng_state(self.coordination_rng.getstate()),
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
        random.setstate(_decode_rng_state(state["global_rng_state"]))
        self.coordination_rng.setstate(_decode_rng_state(state["coordination_rng_state"]))
        logger.info(f"Loaded checkpoint from {path} (resuming at {self.start_iteration})")
