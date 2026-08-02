"""Unit tests for IterationRunner tick cadence and operator selection."""

import asyncio
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.database import Program

from noema.agenthost.mutation import FakeMutationBackend
from noema.agenthost.session import AgentSession
from noema.budget.ledger import TokenLedger
from noema.config import SubstrateConfig
from noema.coordination import NullCoordination
from noema.evolution.iteration_runner import IterationRunner
from noema.substrates.registry import build_substrate_runtime
from tests.test_noema_agent_arm_sweep import (
    EVAL_SCRIPT,
    INITIAL_PROGRAM,
    SpyCoordination,
    _scaffold,
    make_config,
)


class TestChooseOperator(unittest.TestCase):
    def test_choose_operator_legacy_path(self):
        config = make_config(mutation_operators=None, diff_based_evolution=False)
        host = SimpleNamespace(
            config=config,
            mutation_operator_rng=random.Random(0),
            _last_operator_trace={},
        )
        spec = IterationRunner.choose_operator(host)
        self.assertEqual(spec.name, "legacy")
        self.assertEqual(spec.parse_mode, "full_rewrite")
        self.assertEqual(host._last_operator_trace["honored"], "legacy")

    def test_choose_operator_honors_request(self):
        config = make_config(mutation_operators=["e1", "e2", "m1"])
        host = SimpleNamespace(
            config=config,
            mutation_operator_rng=random.Random(0),
            _last_operator_trace={},
        )
        spec = IterationRunner.choose_operator(host, requested="e1")
        self.assertEqual(spec.name, "e1")
        self.assertEqual(host._last_operator_trace["honored"], "e1")
        self.assertIsNone(host._last_operator_trace["ignored"])


class TestGenerationTick(unittest.TestCase):
    def test_accepted_child_emits_shared_host_heartbeat(self):
        config = make_config(max_iterations=1)
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            session = AgentSession(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                coordination=NullCoordination(),
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                stop_children=1,
                mutation_backend=FakeMutationBackend(
                    producer=lambda _req: _scaffold("def f():\n    return 2\n")
                ),
            )
            session.store.add(
                Program(
                    id="initial",
                    code=INITIAL_PROGRAM,
                    language="python",
                    metrics={"combined_score": 0.1},
                ),
                iteration=0,
            )

            async def scenario():
                await IterationRunner.run_iteration(session, 0)

            with self.assertLogs("noema.host", level="INFO") as logs:
                asyncio.run(scenario())
            self.assertTrue(
                any("Child it000000 from parent initial" in line for line in logs.output)
            )
            self.assertTrue(any("via cli/shallow" in line for line in logs.output))

    def test_generation_tick_fires_on_generation_end(self):
        spy = SpyCoordination(NullCoordination())
        config = make_config()
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            session = AgentSession(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                coordination=spy,
                ledger=TokenLedger(total_budget_tokens=1_000_000),
                stop_children=1,
                mutation_backend=FakeMutationBackend(
                    producer=lambda _req: _scaffold("def f():\n    return 2\n")
                ),
            )
            session.store.add(
                Program(
                    id="initial",
                    code=INITIAL_PROGRAM,
                    language="python",
                    metrics={"combined_score": 0.1},
                ),
                iteration=0,
            )
            cadence = session.substrate.steps_per_generation

            async def scenario():
                await IterationRunner.generation_tick(session, cadence - 1)

            before = session.generation
            asyncio.run(scenario())
            self.assertEqual(session.generation, before + 1)
            self.assertIn("on_generation_end", spy.calls)

    def test_generation_tick_not_called_off_boundary(self):
        config = make_config(
            substrate=SubstrateConfig(kind="flat", steps_per_generation=3),
        )
        runtime = build_substrate_runtime(config)
        self.assertEqual(runtime.steps_per_generation, 3)
        iteration = 1
        self.assertNotEqual(
            0,
            (iteration + 1) % runtime.steps_per_generation,
            "iteration=1 should be off-boundary for spg=3",
        )


if __name__ == "__main__":
    unittest.main()
