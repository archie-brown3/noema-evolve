"""Controller vs agent-host parity: generation tick and resulting store state."""

import asyncio
import json
import os
import random
import tempfile
import unittest

from openevolve.config import DatabaseConfig

from noema.agenthost.mutation import FakeMutationBackend
from noema.agenthost.session import AgentSession
from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import NoemaConfig, SubstrateConfig
from noema.controller import NoemaController
from noema.coordination import NullCoordination
from tests.test_noema_agent_arm_sweep import SpyCoordination, _scaffold
from tests.test_noema_controller import EVAL_SCRIPT as CTRL_EVAL
from tests.test_noema_controller import (
    INITIAL_PROGRAM,
    CyclingFakeClient,
)
from tests.test_noema_controller import make_config as make_controller_config


def _parity_config(stop: int = 6) -> NoemaConfig:
    return make_controller_config(
        max_iterations=stop,
        database=DatabaseConfig(
            in_memory=True,
            num_islands=2,
            population_size=50,
            random_seed=42,
            migration_interval=1000,
        ),
        substrate=SubstrateConfig(kind="islands"),
    )


_PROGRAM_FIELDS = (
    "id",
    "code",
    "language",
    "parent_id",
    "generation",
    "iteration_found",
    "changes_description",
)
# Wall-clock timestamp and evaluator stderr are excluded: they are not evolution
# state, and neither can be identical across two runs.
_METADATA_FIELDS = (
    "changes",
    "parent_metrics",
    "coordination",
    "island",
    "operator",
    "source_attempt_id",
)


def _attempt_suffix(attempt_id):
    """Drop the run_id prefix: the two arms must use different output dirs."""
    if not isinstance(attempt_id, str):
        return attempt_id
    return attempt_id.split(":", 1)[-1]


def _store_projection(store) -> str:
    programs = []
    for program in sorted(store.population(), key=lambda item: item.id):
        metadata = {key: program.metadata.get(key) for key in _METADATA_FIELDS}
        metadata["source_attempt_id"] = _attempt_suffix(metadata["source_attempt_id"])
        programs.append(
            {
                **{field: getattr(program, field) for field in _PROGRAM_FIELDS},
                "metrics": program.metrics,
                "metadata": metadata,
            }
        )
    return json.dumps(programs, sort_keys=True, indent=2, default=str)


class TestHarnessTickParity(unittest.TestCase):
    def test_same_on_generation_end_count(self):
        stop = 6
        config = _parity_config(stop)
        codes = [_scaffold(f"def f():\n    return {n}\n") for n in range(2, 2 + stop)]

        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(CTRL_EVAL)

            spy_agent = SpyCoordination(NullCoordination())
            supply = iter(codes)
            agent_session = AgentSession(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "agent_output"),
                coordination=spy_agent,
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                stop_children=stop,
                mutation_backend=FakeMutationBackend(producer=lambda _req: next(supply)),
            )

            spy_ctrl = SpyCoordination(NullCoordination())
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            client = CyclingFakeClient()
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "ctrl_output"),
                mutation_llm=BudgetedLLM(
                    model="fake-model",
                    ledger=ledger,
                    account="mutation",
                    tag="mutate",
                    client=client,
                    retries=0,
                    retry_delay=0.0,
                ),
                coordination=spy_ctrl,
                ledger=ledger,
            )

            async def run_agent():
                await agent_session.run_agent_mode()

            asyncio.run(run_agent())
            asyncio.run(controller.run())

            agent_ticks = spy_agent.calls.count("on_generation_end")
            ctrl_ticks = spy_ctrl.calls.count("on_generation_end")
            self.assertEqual(agent_ticks, ctrl_ticks)
            self.assertGreater(agent_ticks, 0)
            self.assertEqual(agent_session.generation, controller.generation)


class TestHarnessStoreParity(unittest.TestCase):
    """Shallow/shallow agent host must land the same population as the controller."""

    def test_agent_and_controller_restore_same_global_selection_seed(self):
        config = make_controller_config(
            max_iterations=1,
            random_seed=9173,
            database=DatabaseConfig(
                in_memory=True,
                num_islands=2,
                population_size=50,
                random_seed=42,
            ),
            substrate=SubstrateConfig(kind="islands"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(CTRL_EVAL)

            AgentSession(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "agent_output"),
                coordination=NullCoordination(),
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                stop_children=1,
                mutation_backend=FakeMutationBackend(producer=lambda _req: INITIAL_PROGRAM),
            )
            agent_draw = random.random()

            ledger = TokenLedger(total_budget_tokens=1_000_000)
            NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "ctrl_output"),
                mutation_llm=BudgetedLLM(
                    model="fake-model",
                    ledger=ledger,
                    account="mutation",
                    tag="mutate",
                    client=CyclingFakeClient(),
                    retries=0,
                    retry_delay=0.0,
                ),
                coordination=NullCoordination(),
                ledger=ledger,
            )
            controller_draw = random.random()

        expected_draw = random.Random(config.random_seed).random()
        self.assertEqual(agent_draw, expected_draw)
        self.assertEqual(controller_draw, expected_draw)

    def test_store_state_is_identical(self):
        stop = 6
        config = _parity_config(stop)
        # Exactly what CyclingFakeClient's fenced response parses down to, so the
        # two arms differ only in mutation transport.
        codes = [f"def f():\n    return {n}" for n in range(2, 2 + stop)]

        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(CTRL_EVAL)

            # Each arm is built immediately before it runs: constructing the store
            # re-seeds the global RNG that openevolve selection draws from, so a
            # shared build phase would leave the second arm mid-stream.
            supply = iter(codes)
            agent_session = AgentSession(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "agent_output"),
                coordination=NullCoordination(),
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                stop_children=stop,
                mutation_backend=FakeMutationBackend(producer=lambda _req: next(supply)),
            )
            asyncio.run(agent_session.run_agent_mode())

            ledger = TokenLedger(total_budget_tokens=1_000_000)
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "ctrl_output"),
                mutation_llm=BudgetedLLM(
                    model="fake-model",
                    ledger=ledger,
                    account="mutation",
                    tag="mutate",
                    client=CyclingFakeClient(),
                    retries=0,
                    retry_delay=0.0,
                ),
                coordination=NullCoordination(),
                ledger=ledger,
            )
            asyncio.run(controller.run())

            self.assertEqual(
                _store_projection(agent_session.store),
                _store_projection(controller.substrate.store),
            )
            self.assertEqual(agent_session.store.num_programs, stop + 1)


if __name__ == "__main__":
    unittest.main()
