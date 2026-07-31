"""Agent-driven host: the session an agent harness drives through tool calls.

Side research (task 0160). Unlike `NoemaController`, this host does not own a
mutation LLM — mutation is a nested headless CLI (``run_mutation`` →
``MutationBackend``) that returns a deliverable file. Coordination hooks live
in ``CoordinatorSeat``; mutate/admit mechanics in ``BurstRunner``.
"""

import logging
import os
import random
from typing import Any, Dict, List, Optional

from openevolve.database import Program

from noema.budget.ledger import TokenLedger
from noema.config import NoemaConfig
from noema.coordination import CoordinationModule, GenerationContext, NullCoordination
from noema.agenthost.burst_runner import BurstRunner
from noema.agenthost.coordinator_seat import CoordinatorSeat
from noema.agenthost.mutation import MutationBackend
from noema.evolution.evaluator import make_evaluator
from noema.evolution.operators import OperatorSpec
from noema.evolution.prompts import make_prompt_sampler
from noema.substrates.registry import build_substrate_runtime
from noema.trace import AttemptTraceWriter

logger = logging.getLogger(__name__)

_REQUIRED_CALL = {
    "idle": "begin_run",
    "open": "next_target",
    "targeted": "select_parent",
    "parented": "get_brief",
    "briefed": "run_mutation",
    "complete": None,
}


class PhaseError(RuntimeError):
    """A tool was called out of order. Names the call the agent owes instead."""

    def __init__(self, required_call: Optional[str], attempted: str):
        super().__init__(
            f"{attempted} is not available: the run is complete"
            if required_call is None
            else f"{attempted} is not available yet: call {required_call} first"
        )
        self.required_call = required_call
        self.attempted = attempted


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
    ):
        self.config = config
        self.task = task or config.prompt.system_message
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.ledger = ledger or TokenLedger(
            total_budget_tokens=config.budget.total_tokens,
            account_caps=config.budget.account_caps,
            log_path=config.budget.log_path
            or os.path.join(output_dir, "llm_calls.jsonl"),
        )
        self.substrate = build_substrate_runtime(config)
        self.store = self.substrate.store
        self.evaluator = make_evaluator(
            config.evaluator, evaluation_file, suffix=config.file_suffix
        )
        self.coordination = coordination or NullCoordination()
        self.mutation_backend = mutation_backend
        self.sampler = make_prompt_sampler(config.prompt)
        self.mutation_operator_rng = random.Random(config.mutation_operator_seed)
        self.initial_program_code = initial_program_code
        self.stop_children = (
            stop_children if stop_children is not None else config.max_iterations
        )
        self.attempt_tracer = AttemptTraceWriter(
            os.path.join(output_dir, "attempt_trace.jsonl"),
        )

        self.children_accepted = 0
        self.generation = 0
        self.best_fitness_history: List[float] = []
        self.avg_fitness_history: List[float] = []
        self.diversity_history: List[float] = []

        self._phase = "idle"
        self._rejections = 0
        self._mutation_attempts = 0
        self._iteration = 0
        self._target_scope: Optional[int] = None
        self._operator_hint: Optional[str] = None
        self._operator_spec: Optional[OperatorSpec] = None
        self._last_operator_trace: Dict[str, Any] = {}
        self._parent2 = None
        self._selection = None
        self._context: Optional[GenerationContext] = None
        self._advice = None
        self._base_prompt: Optional[Dict[str, str]] = None
        self._prompt: Optional[Dict[str, str]] = None
        self._retry_brief: Optional[str] = None
        self._last_error: Optional[str] = None

        self._runner = BurstRunner(self)
        self._seat = CoordinatorSeat(self)

    async def begin_run(self) -> Dict[str, Any]:
        if self.store.num_programs == 0:
            metrics = await self.evaluator.evaluate_program(
                self.initial_program_code, "initial"
            )
            program = Program(
                id="initial",
                code=self.initial_program_code,
                language=self.config.language,
                metrics=metrics,
                iteration_found=0,
            )
            self.store.add(program, iteration=0)
        self._phase = "open"
        return {"children_accepted": self.children_accepted, "stop_children": self.stop_children}

    def next_target(self) -> Dict[str, Any]:
        if self._phase == "complete":
            return {"status": "complete", **self.run_status()}
        self._require("open", "next_target")
        self._target_scope = self.substrate.target_scope(self._iteration)
        self._rejections = 0
        self._mutation_attempts = 0
        self._retry_brief = None
        self._last_error = None
        self._base_prompt = None
        self._prompt = None
        self._operator_spec = None
        self._parent2 = None
        self._phase = "targeted"
        return {"status": "open", "iteration": self._iteration, "target_scope": self._target_scope}

    def run_status(self) -> Dict[str, Any]:
        return {
            "children_accepted": self.children_accepted,
            "stop_children": self.stop_children,
            "stopped": self._phase == "complete",
            "generation": self.generation,
            "tokens_spent": self.ledger.spent(),
        }

    def select_parent(self) -> Dict[str, Any]:
        self._require("targeted", "select_parent")
        hints, operator_hint = self._seat.sampling_hints()
        return self._runner.select_parent(hints, operator_hint)

    async def get_brief(self) -> Dict[str, Any]:
        self._require("parented", "get_brief")
        return await self._seat.plan_brief()

    async def run_mutation(self, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        self._require("briefed", "run_mutation")
        if timeout_s is None:
            return await self._runner.run_mutation()
        return await self._runner.run_mutation(timeout_s=timeout_s)

    async def submit_child(self, code: str) -> Dict[str, Any]:
        self._require("briefed", "submit_child")
        outcome = await self._runner.submit_child(code)
        return await self._seat.finalize_child(outcome)

    async def run_agent_mode(self) -> Dict[str, Any]:
        """Chain bursts until stop_children; no external input between bursts."""
        await self.begin_run()
        burst_cap = self.substrate.steps_per_generation
        while self.children_accepted < self.stop_children:
            burst_n = 0
            while self.children_accepted < self.stop_children and burst_n < burst_cap:
                target = self.next_target()
                if target.get("status") == "complete":
                    return self.run_status()
                hints, operator_hint = self._seat.sampling_hints()
                self._runner.select_parent(hints, operator_hint)
                await self._seat.plan_brief()
                while True:
                    result = await self._runner.run_mutation()
                    status = result.get("status")
                    if status == "accepted":
                        burst_n += 1
                        if self._iteration % burst_cap == 0:
                            break
                        break
                    if status in ("mutation_failed", "rejected"):
                        continue
                    break
            if self.children_accepted >= self.stop_children:
                break
        if self.children_accepted >= self.stop_children:
            self._phase = "complete"
        return self.run_status()

    def _require(self, phase: str, attempted: str) -> None:
        if self._phase != phase:
            raise PhaseError(_REQUIRED_CALL[self._phase], attempted)
