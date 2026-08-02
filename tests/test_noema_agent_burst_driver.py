"""Burst driver tests for run_agent_mode (task 0163)."""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.agenthost.config import AgentCliConfig, AgentConfig
from noema.agenthost.factory import create_agent_session
from noema.agenthost.mutation import FakeMutationBackend
from noema.agenthost.session import AgentSession
from noema.budget.ledger import TokenLedger
from noema.config import BudgetConfig, SubstrateConfig
from noema.evolution.iteration_runner import IterationRunner
from tests.test_noema_agent_arm_sweep import (
    INITIAL_PROGRAM,
    SEED_CHILDREN,
    SpyCoordination,
    _scaffold,
    build_arm,
    make_config,
)


def make_burst_session(tmp, key: str, codes, stop_children: int = 4, config=None):
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as f:
        from tests.test_noema_agent_arm_sweep import EVAL_SCRIPT

        f.write(EVAL_SCRIPT)
    spy = SpyCoordination(build_arm(key))
    supply = iter(codes)
    session = AgentSession(
        config=config or make_config(),
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        coordination=spy,
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        stop_children=stop_children,
        task="Maximise the number the program returns.",
        mutation_backend=FakeMutationBackend(producer=lambda _req: next(supply)),
    )
    return session, spy


class TestRunAgentMode(unittest.TestCase):
    def test_run_agent_mode_reaches_stop_children(self):
        stop = 3
        codes = [_scaffold(f"def f():\n    return {n}\n") for n in (2, 3, 4)]
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_burst_session(tmp, "null", codes, stop_children=stop)

            async def scenario():
                return await session.run_agent_mode()

            status = asyncio.run(scenario())
            self.assertEqual(status["children_accepted"], stop)
            self.assertTrue(status["stopped"])
            self.assertIn("sampling_request", spy.calls)
            self.assertIn("advise", spy.calls)
            self.assertIn("report_result", spy.calls)

    def test_run_agent_mode_pe_inserts_after_generation(self):
        stop = 6
        codes = list(SEED_CHILDREN) + [
            _scaffold("def f():\n    return 8\n"),
            _scaffold("def f():\n    return 9\n"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_burst_session(tmp, "pe", codes, stop_children=stop)
            cadence = session.substrate.steps_per_generation

            async def scenario():
                await session.run_agent_mode()
                return session.store.num_programs

            n_programs = asyncio.run(scenario())
            self.assertGreater(
                n_programs,
                1 + stop,
                "PE fired on_generation_end but no proposal was inserted",
            )
            self.assertIn("on_generation_end", spy.calls)

    def test_run_agent_mode_generation_boundary_fires_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_burst_session(
                tmp,
                "null",
                [_scaffold("def f():\n    return 2\n"), _scaffold("def f():\n    return 3\n")],
                stop_children=2,
            )
            cadence = session.substrate.steps_per_generation

            async def scenario():
                await session.run_agent_mode()

            asyncio.run(scenario())
            self.assertIn("on_generation_end", spy.calls)
            self.assertGreaterEqual(session.generation, 1)
            self.assertEqual(session._iteration, cadence)

    def test_no_generation_tick_before_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_burst_session(
                tmp,
                "null",
                [_scaffold("def f():\n    return 2\n")],
                stop_children=1,
                config=make_config(
                    substrate=SubstrateConfig(kind="flat", steps_per_generation=5),
                ),
            )

            async def scenario():
                await session.run_agent_mode()

            asyncio.run(scenario())
            self.assertNotIn("on_generation_end", spy.calls)
            self.assertEqual(session.children_accepted, 1)

    def test_no_accepted_child_is_bounded_by_max_iterations(self):
        config = make_config(max_iterations=2)
        calls = 0

        async def reject_without_raising(_session, _iteration, **_kwargs):
            nonlocal calls
            calls += 1
            if calls > config.max_iterations:
                raise AssertionError("run_agent_mode exceeded its no-accept bound")

        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_burst_session(
                tmp,
                "null",
                [],
                stop_children=1,
                config=config,
            )
            with patch.object(IterationRunner, "run_iteration", reject_without_raising):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "failed to accept a child after 2 attempts",
                ):
                    asyncio.run(session.run_agent_mode())

        self.assertEqual(calls, config.max_iterations)


class TestRunAgentModeSubstrateSmoke(unittest.TestCase):
    """B4: burst path with non-default islands substrate."""

    def test_islands_target_scope_cycles_under_burst_driver(self):
        stop = 4
        codes = [_scaffold(f"def f():\n    return {n}\n") for n in range(2, 2 + stop)]
        cfg = make_config(
            database=DatabaseConfig(
                in_memory=True,
                num_islands=2,
                population_size=50,
                random_seed=42,
                migration_interval=1000,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                from tests.test_noema_agent_arm_sweep import EVAL_SCRIPT

                f.write(EVAL_SCRIPT)
            spy = SpyCoordination(build_arm("null"))
            supply = iter(codes)
            session = AgentSession(
                config=cfg,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                coordination=spy,
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                stop_children=stop,
                task="Maximise the number the program returns.",
                mutation_backend=FakeMutationBackend(producer=lambda _req: next(supply)),
            )

            async def scenario():
                await session.run_agent_mode()
                return session.store.num_programs

            asyncio.run(scenario())
            self.assertIn("on_generation_end", spy.calls)
            self.assertEqual(session.children_accepted, stop)

    def test_factory_built_session_run_agent_mode(self):
        stop = 2
        codes = [
            _scaffold("def f():\n    return 2\n"),
            _scaffold("def f():\n    return 3\n"),
        ]
        cfg = AgentConfig(
            noema=make_config(
                max_iterations=stop,
                database=DatabaseConfig(
                    in_memory=True,
                    num_islands=2,
                    population_size=50,
                    random_seed=42,
                    migration_interval=1000,
                ),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30),
                budget=BudgetConfig(total_tokens=1_000_000),
            ),
            stop_children=stop,
            mutation_cli=AgentCliConfig(kind="opencode"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                from tests.test_noema_agent_arm_sweep import EVAL_SCRIPT

                f.write(EVAL_SCRIPT)
            spy = SpyCoordination(build_arm("null"))
            supply = iter(codes)
            session = create_agent_session(
                cfg,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                coordination=spy,
                mutation_backend=FakeMutationBackend(producer=lambda _req: next(supply)),
            )

            async def scenario():
                return await session.run_agent_mode()

            status = asyncio.run(scenario())
            self.assertEqual(status["children_accepted"], stop)
            self.assertIn("on_generation_end", spy.calls)
