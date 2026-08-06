"""Agent-driven host: one evolutionary run driven by ``run_agent_mode``.

Side research (task 0160). Unlike `NoemaController`, this host does not own a
mutation LLM — mutation is a nested headless CLI (``MutationBackend``) that
returns a deliverable file. Iteration semantics are shared with the controller
through ``IterationRunner``, which is what makes the two arms comparable.
"""

import os
import random
import threading
import warnings
from collections import deque
from typing import Any, Callable, Dict, List, Optional

from openevolve.database import Program  # type: ignore[import-untyped]

from noema.agenthost.mutation import MutationBackend
from noema.agenthost.mutation_transport import (
    AgentMutationTransport,
    MutationTransport,
    MutationTransportFailure,
)
from noema.budget.ledger import TokenLedger
from noema.config import NoemaConfig
from noema.coordination import CoordinationModule, GenerationContext, NullCoordination
from noema.coordination.escalation import EscalationPolicy
from noema.evolution.evaluator import make_evaluator
from noema.evolution.iteration_runner import IterationRunner
from noema.evolution.operators import OperatorSpec
from noema.evolution.prompts import make_prompt_sampler
from noema.logging import setup_run_logging
from noema.substrates.registry import build_substrate_runtime
from noema.trace import AttemptTraceWriter


class AgentSessionAborted(RuntimeError):
    """The run monitor requested that this agency session stop."""


class AgentSession:
    """One evolutionary run, advanced by an agent's tool calls."""

    def __init__(
        self,
        config: NoemaConfig,
        evaluation_file: str,
        initial_program_code: str,
        output_dir: str = "noema_agent_output",
        coordination: Optional[CoordinationModule] = None,
        ledger: Optional[TokenLedger] = None,
        stop_children: Optional[int] = None,
        task: Optional[str] = None,
        mutation_backend: Optional[MutationBackend] = None,
        mutation_timeout_s: float = 120.0,
        attempt_trace_callback: Optional[Callable[[dict], None]] = None,
        mutation_via: str = "cli/shallow",
        console_suspended: bool = False,
    ):
        self.config = config
        self.task = task or config.prompt.system_message
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.run_logging = setup_run_logging(
            config.logging, output_dir, console_suspended=console_suspended
        )
        self.mutation_via = mutation_via

        self.ledger = ledger or TokenLedger(
            total_budget_tokens=config.budget.total_tokens,
            account_caps=config.budget.account_caps,
            log_path=config.budget.log_path or os.path.join(output_dir, "llm_calls.jsonl"),
        )
        self.substrate = build_substrate_runtime(config)
        random.seed(config.random_seed)
        self.store = self.substrate.store
        self.evaluator = make_evaluator(
            config.evaluator, evaluation_file, suffix=config.file_suffix
        )
        self.coordination = coordination or NullCoordination()
        self.mutation_backend = mutation_backend
        self.sampler = make_prompt_sampler(config.prompt)
        self.mutation_operator_rng = random.Random(config.mutation_operator_seed)
        self.initial_program_code = initial_program_code
        self.stop_children = stop_children if stop_children is not None else config.max_iterations
        self.attempt_tracer = AttemptTraceWriter(
            os.path.join(output_dir, "attempt_trace.jsonl"),
            on_write=attempt_trace_callback,
        )
        self._abort_requested = threading.Event()

        self.children_accepted = 0
        self.generation = 0
        self.best_fitness_history: List[float] = []
        self.avg_fitness_history: List[float] = []
        self.diversity_history: List[float] = []
        self.generation_log: List[Dict[str, Any]] = []
        self.escalation: Optional[EscalationPolicy] = None
        esc_window = 10
        escalation_config = config.coordination.escalation
        if escalation_config is not None:
            if escalation_config.trigger == "budget_fraction":
                warnings.warn(
                    "NoemaAgent cannot apply budget_fraction escalation because "
                    "coding-CLI mutation tokens are not metered; escalation is disabled.",
                    UserWarning,
                    stacklevel=2,
                )
            else:
                self.escalation = EscalationPolicy(
                    escalation_config,
                    rng=random.Random(config.random_seed + 5),
                )
                esc_window = escalation_config.window
        self._esc_recent_valid: deque = deque(maxlen=esc_window)
        self._esc_recent_scores: deque = deque(maxlen=esc_window)
        self._current_advice_call_ids: List[str] = []

        self._mutation_attempts = 0
        self._driver_mutation_attempts = 0
        self._iteration = 0
        self._target_scope: Optional[int] = None
        self._operator_spec: Optional[OperatorSpec] = None
        self._last_operator_trace: Dict[str, Any] = {}
        self._parent2 = None
        self._selection = None
        self._context: Optional[GenerationContext] = None
        self._advice = None
        self._retry_brief: Optional[str] = None
        self._materialized_child: Optional[tuple] = None

        self._mutation_transport = AgentMutationTransport(
            self,
            timeout_s=mutation_timeout_s,
        )

    @property
    def mutation_transport(self) -> MutationTransport:
        return self._mutation_transport

    @property
    def population_store(self):
        return self.store

    @property
    def run_iteration_limit(self) -> int:
        return self.stop_children

    def on_mutation_child_accepted(self, child: Program) -> None:
        self.children_accepted += 1
        self._iteration += 1

    def abort(self) -> None:
        """Request graceful host shutdown after the active transport returns."""

        self._abort_requested.set()
        abort_backend = getattr(self.mutation_backend, "abort", None)
        if callable(abort_backend):
            abort_backend()
        self._abort_deep_coordination()

    @property
    def aborted(self) -> bool:
        return self._abort_requested.is_set()

    def raise_if_aborted(self) -> None:
        if self.aborted:
            raise AgentSessionAborted("agency run aborted by operator")

    def _abort_deep_coordination(self) -> None:
        seen = set()
        for llm in (
            getattr(self.coordination, "llm", None),
            getattr(self.coordination, "_paradigm_llm", None),
            getattr(self.coordination, "_variant_llm", None),
        ):
            if id(llm) in seen:
                continue
            seen.add(id(llm))
            abort = getattr(llm, "abort", None)
            if callable(abort):
                abort()

    async def begin_run(self) -> Dict[str, Any]:
        if self.store.num_programs == 0:
            metrics = await self.evaluator.evaluate_program(self.initial_program_code, "initial")
            program = Program(
                id="initial",
                code=self.initial_program_code,
                language=self.config.language,
                metrics=metrics,
                iteration_found=0,
            )
            self.store.add(program, iteration=0)
        return {"children_accepted": self.children_accepted, "stop_children": self.stop_children}

    def run_status(self) -> Dict[str, Any]:
        return {
            "children_accepted": self.children_accepted,
            "stop_children": self.stop_children,
            "stopped": self.children_accepted >= self.stop_children,
            "generation": self.generation,
            "tokens_spent": self.ledger.spent(),
        }

    async def run_agent_mode(self) -> Dict[str, Any]:
        """Chain iterations until stop_children; no external input between steps.

        Iteration body and generation tick come from ``IterationRunner``, so the
        agent host and the controller advance the population identically.
        """
        self.raise_if_aborted()
        await self.begin_run()
        iteration = 0
        while self.children_accepted < self.stop_children:
            self.raise_if_aborted()
            self._driver_mutation_attempts = 0
            accepted_before = self.children_accepted
            attempts_without_acceptance = 0
            last_transport_error: Optional[MutationTransportFailure] = None
            while self.children_accepted == accepted_before:
                if attempts_without_acceptance >= self.config.max_iterations:
                    message = (
                        f"iteration {iteration} failed to accept a child after "
                        f"{self.config.max_iterations} attempts"
                    )
                    if last_transport_error is not None:
                        raise RuntimeError(
                            f"{message}: {last_transport_error}"
                        ) from last_transport_error
                    raise RuntimeError(message)
                try:
                    self.raise_if_aborted()
                    await IterationRunner.run_iteration(
                        self,
                        iteration,
                        attempt_offset=self._driver_mutation_attempts,
                    )
                except MutationTransportFailure as exc:
                    self.raise_if_aborted()
                    last_transport_error = exc
                    attempts_without_acceptance += 1
                    continue
                attempts_without_acceptance += 1
            if (iteration + 1) % self.substrate.steps_per_generation == 0:
                await IterationRunner.generation_tick(self, iteration)
            iteration += 1
        return self.run_status()
