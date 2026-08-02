"""
The noema single-process controller.

Single-process, strictly sequential: sample → advise → prompt → mutate → parse →
evaluate → add → report → generation tick → checkpoint. Coordination state lives
in this process (the released HiFo-Prompt lost its credit-assignment feedback to
joblib subprocess copies), and the coordination-OFF vs
coordination-ON arms differ ONLY in which CoordinationModule is plugged in.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from collections import deque
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openevolve.database import Program
from openevolve.evolution_trace import EvolutionTracer

from noema.budget.ledger import (
    COORDINATION_ACCOUNT,
    MUTATION_ACCOUNT,
    BudgetExhausted,
    TokenLedger,
)
from noema.budget.llm import BudgetedLLM, FatalProviderError, build_budgeted_llm
from noema.config import NoemaConfig
from noema.coordination import (
    CoordinationModule,
    GenerationContext,
    build_coordination_module,
)
from noema.coordination.escalation import EscalationContext, EscalationPolicy
from noema.evolution.evaluator import make_evaluator
from noema.evolution.iteration_runner import IterationRunner
from noema.evolution.operators import OperatorSpec
from noema.evolution.prompts import make_prompt_sampler
from noema.substrates.registry import build_substrate_runtime
from noema.trace import AttemptTraceWriter, git_provenance, sha256_file

if TYPE_CHECKING:
    from noema.agenthost.mutation_transport import MutationTransport

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
    Hosts the shared iteration runner in-process; borrows OpenEvolve's
    database/evaluator/prompt sampler via the substrate adapters and calls the
    coordination hooks.

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
        frozen_config_path = os.path.join(output_dir, FROZEN_CONFIG_FILE)
        self.attempt_tracer = AttemptTraceWriter(
            os.path.join(output_dir, "attempt_trace.jsonl"),
            run_id=os.path.basename(os.path.abspath(output_dir)),
            config_sha256=sha256_file(frozen_config_path),
            git_provenance=git_provenance(os.path.dirname(os.path.abspath(__file__))),
        )

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

        self.mutation_llm = mutation_llm or build_budgeted_llm(
            config.llm.mutation,
            ledger=self.ledger,
            account=MUTATION_ACCOUNT,
            tag="mutate",
        )

        if coordination is not None:
            self.coordination = coordination
        else:
            coordination_llm = build_budgeted_llm(
                config.llm.coordination,
                ledger=self.ledger,
                account=COORDINATION_ACCOUNT,
                tag=f"{config.coordination.module}.coordination",
            )
            # Domain constraints (e.g. "explicit constructor, not iterative
            # search") are problem context, not search mechanics — safe for a
            # coordination module to see. Modules that don't look for this key
            # ignore it, like any other mechanism-specific coordination param.
            coordination_params = dict(config.coordination.params)
            coordination_params.setdefault("domain_context", config.prompt.system_message)
            # Task 0107: the model name a module may request via Advice.model to
            # escalate a mutation generation. Today the frontier coordination
            # seat (PR #46); a bootstrap value, not a base.py field — modules
            # that don't escalate never read it.
            coordination_params.setdefault("escalation_model", config.llm.coordination.model)
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
            # Task 0110: opt-in heavy/light model tiering. A module MAY expose
            # set_paradigm_llm/set_variant_llm (duck-typed, not part of the
            # CoordinationModule ABC — base.py stays untouched, same pattern as
            # PES's build_retry_prompt). Config keys are read from the SAME
            # coordination.params dict every module already receives; a run
            # that names no override is unaffected (both tiers stay the
            # already-injected coordination_llm — PR #61 behaviour).
            self._wire_alternate_tier(
                "set_paradigm_llm",
                coordination_params.get("paradigm_model"),
                config,
                tag=f"{config.coordination.module}.paradigm",
            )
            self._wire_alternate_tier(
                "set_variant_llm",
                coordination_params.get("variant_model"),
                config,
                tag=f"{config.coordination.module}.variant",
            )

        self.initial_program_code = initial_program_code

        # Model escalation (task 0107, "B"): a controller-owned modifier layered
        # on ANY arm's Advice. Its own RNG stream (random_seed + 5, distinct from
        # coordination +1 / operators +2 / selection +3 / cvt +4) so turning
        # escalation on/off never perturbs another consumer's draws. None =
        # escalation off = byte-identical to today.
        self.escalation: Optional[EscalationPolicy] = None
        esc_window = 10
        if config.coordination.escalation is not None:
            self.escalation = EscalationPolicy(
                config.coordination.escalation,
                rng=random.Random(config.random_seed + 5),
            )
            esc_window = config.coordination.escalation.window
        # Rolling per-mutation signals for the invalidity / diversity triggers,
        # updated once per iteration and read at the NEXT iteration's advise.
        self._esc_recent_valid: deque = deque(maxlen=esc_window)
        self._esc_recent_scores: deque = deque(maxlen=esc_window)

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
        # Last operator requested/honored/ignored (task 0073); logged per tick.
        self._last_operator_trace: Dict[str, Any] = {
            "requested": None,
            "honored": None,
            "ignored": None,
        }
        self._current_advice_call_ids: List[str] = []

    @property
    def mutation_transport(self) -> MutationTransport:
        return self.mutation_llm

    @property
    def population_store(self):
        return self.db

    @property
    def run_iteration_limit(self) -> int:
        return self.config.max_iterations

    def _wire_alternate_tier(
        self, setter_name: str, model_name: Optional[str], config: NoemaConfig, *, tag: str
    ) -> None:
        """Build and inject an alternate-model BudgetedLLM if BOTH the module
        supports the duck-typed setter AND a distinct model was configured
        (task 0110). No-op otherwise — default tiering is "unchanged"."""
        if not model_name or model_name == config.llm.coordination.model:
            return
        setter = getattr(self.coordination, setter_name, None)
        if setter is None:
            return
        setter(
            build_budgeted_llm(
                config.llm.coordination,
                ledger=self.ledger,
                account=COORDINATION_ACCOUNT,
                tag=tag,
                model=model_name,
            )
        )

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
                except FatalProviderError as e:
                    # 0103 scope addition: the 2026-07-17 temp-0.7 sweep died on
                    # OpenRouter 402s as raw unhandled tracebacks between
                    # checkpoints, discarding up to checkpoint_interval-1
                    # iterations of progress each time. Stop cleanly instead —
                    # the checkpoint below preserves everything up to here.
                    logger.error(f"Stopping at iteration {iteration}: {e}")
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

    def _choose_operator(self, requested: Optional[str] = None) -> OperatorSpec:
        return IterationRunner.choose_operator(self, requested)

    async def _run_iteration(self, iteration: int) -> None:
        await IterationRunner.run_iteration(self, iteration)

    def _write_attempt_trace(self, *args, **kwargs) -> None:
        IterationRunner._write_attempt_trace(self, *args, **kwargs)

    @staticmethod
    def _program_trace_snapshot(program: Program) -> Dict[str, Any]:
        return IterationRunner._program_trace_snapshot(program)

    def _iteration_ledger_metadata(self, iteration: int) -> Dict[str, Any]:
        return IterationRunner._iteration_ledger_metadata(self, iteration)

    def _build_retry_suffix(self, error_text: str, attempt: int) -> str:
        return IterationRunner._build_retry_suffix(error_text, attempt)

    async def _build_retry_prompt(self, base_prompt, advice, error_text, attempt, ctx):
        return await IterationRunner._build_retry_prompt(
            self, base_prompt, advice, error_text, attempt, ctx
        )

    def _parse_response(self, response: str, parent_code: str, operator: OperatorSpec):
        return IterationRunner._parse_response(self, response, parent_code, operator)

    async def _generation_tick(self, iteration: int) -> None:
        await IterationRunner.generation_tick(self, iteration)

    async def _apply_intervention(self, intervention, iteration: int) -> None:
        await IterationRunner._apply_intervention(self, intervention, iteration)

    def _build_escalation_context(self, iteration: int) -> EscalationContext:
        return IterationRunner._build_escalation_context(self, iteration)

    def _record_escalation_signal(self, valid: bool, score: Optional[float]) -> None:
        IterationRunner._record_escalation_signal(self, valid, score)

    def _update_histories(self) -> None:
        IterationRunner._update_histories(self)

    def _make_context(
        self,
        iteration: int,
        island: int,
        parent: Optional[Program],
        inspirations: List[Program],
        global_scope: bool = False,
        operator: Optional[str] = None,
    ) -> GenerationContext:
        return IterationRunner._make_context(
            self,
            iteration,
            island,
            parent,
            inspirations,
            global_scope=global_scope,
            operator=operator,
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
            "mutation_operator_rng_state": _encode_rng_state(self.mutation_operator_rng.getstate()),
        }
        if self.escalation is not None:
            state["escalation"] = self.escalation.state_dict()
            state["escalation_rng_state"] = _encode_rng_state(self.escalation.rng.getstate())
            state["escalation_recent_valid"] = list(self._esc_recent_valid)
            state["escalation_recent_scores"] = list(self._esc_recent_scores)
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
        if self.escalation is not None and "escalation" in state:
            self.escalation.load_state_dict(state["escalation"])
            self.escalation.rng.setstate(_decode_rng_state(state["escalation_rng_state"]))
            self._esc_recent_valid = deque(
                state["escalation_recent_valid"], maxlen=self._esc_recent_valid.maxlen
            )
            self._esc_recent_scores = deque(
                state["escalation_recent_scores"], maxlen=self._esc_recent_scores.maxlen
            )
        logger.info(f"Loaded checkpoint from {path} (resuming at {self.start_iteration})")
