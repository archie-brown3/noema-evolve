"""Agent-driven host: the session an agent harness drives through tool calls.

Side research (task 0160). Unlike `NoemaController`, this host does not own a
mutation LLM — the agent harness authors each child itself and hands the finished
code back through `submit_child`. The host keeps everything else: substrate
selection, evaluation, insertion, and the coordination hooks, in that order.
"""

import logging
import os
from dataclasses import replace as dataclass_replace
from typing import Any, Dict, List, Optional

from openevolve.database import Program

from noema.budget.ledger import TokenLedger
from noema.config import NoemaConfig
from noema.coordination import (
    CoordinationModule,
    GenerationContext,
    NullCoordination,
    Outcome,
    SelectionContext,
)
from noema.agenthost.brief import render_brief
from noema.evolution.evaluator import make_evaluator
from noema.substrates.registry import build_substrate_runtime

logger = logging.getLogger(__name__)

# The call the agent owes before the run can advance, keyed by current phase.
_REQUIRED_CALL = {
    "idle": "begin_run",
    "open": "next_target",
    "targeted": "select_parent",
    "parented": "get_brief",
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
    ):
        self.config = config
        # The task objective the agent works to; the controller's equivalent is
        # the system message it feeds openevolve's prompt sampler.
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
        self.initial_program_code = initial_program_code
        self.stop_children = (
            stop_children if stop_children is not None else config.max_iterations
        )

        self.children_accepted = 0
        self.generation = 0
        self.best_fitness_history: List[float] = []
        self.avg_fitness_history: List[float] = []
        self.diversity_history: List[float] = []

        self._phase = "idle"
        self._rejections = 0
        self._iteration = 0
        self._target_scope: Optional[int] = None
        self._selection = None
        self._context: Optional[GenerationContext] = None
        self._advice = None

    # ------------------------------------------------------------ tool surface

    async def begin_run(self) -> Dict[str, Any]:
        """Seed the population with the evaluated initial program."""
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
        """Open a target: which region of the population the next child is for.

        Reports completion rather than raising: the agent's loop ends here, and a
        terminal state is not an agent error.
        """
        if self._phase == "complete":
            return {"status": "complete", **self.run_status()}
        self._require("open", "next_target")
        self._target_scope = self.substrate.target_scope(self._iteration)
        self._rejections = 0
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
        """Draw the parent (and inspirations) via the substrate's policy."""
        self._require("targeted", "select_parent")
        request = self.coordination.sampling_request(
            SelectionContext(
                iteration=self._iteration,
                generation=self.generation,
                scope_id=self._target_scope,
                local_population=self.store.snapshot(
                    self._target_scope, limit=self.config.num_top_programs
                ),
                global_population=self.store.snapshot(
                    None, limit=self.config.num_top_programs
                ),
            )
        )
        hints = {k: v for k, v in request.hints.items() if k != "operator"}
        self.substrate.set_tokens_spent(self.ledger.spent())
        self._selection = self.substrate.select(
            target_scope=self._target_scope,
            num_inspirations=self.config.num_inspirations,
            hints=hints,
        )
        parent = self._selection.parent
        self._phase = "parented"
        return {"parent_id": parent.id, "parent_code": parent.code}

    async def get_brief(self) -> Dict[str, Any]:
        """Fire the coordination advice hook and hand the agent its instructions."""
        self._require("parented", "get_brief")
        self._context = self._make_context()
        self._advice = await self.coordination.advise(self._context)
        parent = self._selection.parent
        self._phase = "briefed"
        return {
            "brief": render_brief(
                task=self.task,
                parent_code=parent.code,
                parent_metrics=parent.metrics,
                coordination_block=self._advice.prompt_block or "",
            )
        }

    async def submit_child(self, code: str) -> Dict[str, Any]:
        """Evaluate, store, and report a child the agent authored."""
        self._require("briefed", "submit_child")
        parent = self._selection.parent
        child_id = f"it{self._iteration:06d}"
        metrics = await self.evaluator.evaluate_program(code, child_id)
        artifacts = self.evaluator.get_pending_artifacts(child_id)

        # "error" is the reserved failure key in the evaluator metrics contract.
        if (not metrics) or ("error" in metrics):
            return await self._reject(parent, metrics, artifacts)

        child = Program(
            id=child_id,
            code=code,
            language=self.config.language,
            parent_id=parent.id,
            generation=parent.generation + 1,
            metrics=metrics,
            iteration_found=self._iteration,
            metadata={
                "parent_metrics": parent.metrics,
                "coordination": self._advice.attribution,
                "island": self._target_scope,
            },
        )
        self.substrate.on_child_accepted(
            parent=parent,
            child=child,
            step_size=min(1.0, (self._iteration + 1) / max(1, self.stop_children)),
        )
        self.store.add(child, iteration=self._iteration, target_scope=self._target_scope)
        self.coordination.report_result(
            self._context,
            child=child,
            attribution=self._advice.attribution,
            eval_failed=False,
            outcome=Outcome.ACCEPTED,
        )
        self.children_accepted += 1
        self._iteration += 1
        self._phase = "complete" if self.children_accepted >= self.stop_children else "open"
        if self._iteration % self.substrate.steps_per_generation == 0:
            await self._generation_tick()
        return {
            "status": "accepted",
            "program_id": child.id,
            "parent_id": parent.id,
            "metrics": dict(metrics),
        }

    async def _reject(self, parent, metrics, artifacts) -> Dict[str, Any]:
        """Failed evaluation: report it, keep the target open for another attempt."""
        self._rejections += 1
        error_text = (metrics or {}).get("error") or (artifacts or {}).get(
            "stderr", "evaluation failed: unknown error"
        )
        self.substrate.on_child_rejected(parent=parent, child=None, eval_failed=True)
        self.coordination.report_result(
            self._context,
            child=None,
            attribution=self._advice.attribution,
            eval_failed=True,
            outcome=Outcome.EVAL_ERROR,
        )
        retry_brief = await self.coordination.retry_advice(
            self._context, error_text, self._rejections
        )
        return {
            "status": "rejected",
            "error": error_text,
            "retry_brief": retry_brief,
            "rejections_for_target": self._rejections,
        }

    # -------------------------------------------------------- generation tick

    async def _generation_tick(self) -> None:
        """Host-fired, not agent-invoked: an arm's population-scale hook must run
        on the substrate's cadence regardless of what the agent remembers to call.
        """
        self.generation += 1
        self._update_histories()
        # A global event: the module sees the whole population, not one scope.
        ctx = dataclass_replace(
            self._make_context(),
            parent=None,
            inspirations=(),
            local_population=self.store.snapshot(None),
            global_population=self.store.snapshot(None),
            generation=self.generation,
            best_fitness_history=list(self.best_fitness_history),
            avg_fitness_history=list(self.avg_fitness_history),
            diversity_history=list(self.diversity_history),
        )
        await self.coordination.on_generation_end(ctx)
        self.store.end_generation()

    def _update_histories(self) -> None:
        best = self.store.best_program()
        self.best_fitness_history.append(self.store.fitness(best) if best else 0.0)
        all_fitnesses = self.store.all_fitnesses()
        self.avg_fitness_history.append(
            sum(all_fitnesses) / len(all_fitnesses) if all_fitnesses else 0.0
        )
        top = self.store.top_programs(10)
        self.diversity_history.append(
            len(set(program.code for program in top)) / len(top) if top else 0.0
        )

    # ----------------------------------------------------------------- context

    def _require(self, phase: str, attempted: str) -> None:
        if self._phase != phase:
            raise PhaseError(_REQUIRED_CALL[self._phase], attempted)

    def _make_context(self) -> GenerationContext:
        parent = self._selection.parent if self._selection else None
        inspirations = list(self._selection.inspirations) if self._selection else []
        limit = self.config.num_top_programs
        return GenerationContext(
            iteration=self._iteration,
            generation=self.generation,
            scope_id=self._target_scope,
            parent=self.store.view(parent) if parent else None,
            inspirations=self.store.views(inspirations),
            local_population=self.store.snapshot(self._target_scope, limit=limit),
            global_population=self.store.snapshot(None, limit=limit),
            best_fitness_history=list(self.best_fitness_history),
            avg_fitness_history=list(self.avg_fitness_history),
            diversity_history=list(self.diversity_history),
        )
