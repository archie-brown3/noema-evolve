"""AgentConfig projection and factory (task 0162)."""

import os
import tempfile
import unittest

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

from noema.agenthost.config import (
    AgentConfig,
    MutationCliConfig,
    agent_config_from_noema,
    validate_agent_config,
)
from noema.agenthost.factory import create_agent_session
from noema.agenthost.mutation import FakeMutationBackend
from noema.config import BudgetConfig, NoemaConfig
from noema.coordination import NullCoordination
from noema.substrates.registry import build_substrate_runtime

from tests.test_noema_agent_arm_sweep import INITIAL_PROGRAM, _scaffold, EVAL_SCRIPT


class TestAgentConfig(unittest.TestCase):
    def test_valid_config_passes(self):
        validate_agent_config(AgentConfig())

    def test_bad_mutation_cli_kind_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(mutation_cli=MutationCliConfig(kind="invalid"))

    def test_agent_config_from_noema_drops_escalation(self):
        noema = NoemaConfig(
            max_iterations=5,
            prompt=PromptConfig(use_template_stochasticity=False),
        )
        agent = agent_config_from_noema(noema, kind="opencode")
        self.assertEqual(agent.max_iterations, 5)
        self.assertEqual(agent.coordination.module, noema.coordination.module)
        self.assertFalse(hasattr(agent.coordination, "escalation"))
        runtime = agent.to_runtime_noema()
        self.assertIsNone(runtime.coordination.escalation)
        build_substrate_runtime(runtime)

    def test_stop_children_alias(self):
        cfg = AgentConfig(max_iterations=10, stop_children=None)
        self.assertEqual(cfg.resolved_stop_children(), 10)
        cfg2 = AgentConfig(max_iterations=10, stop_children=3)
        self.assertEqual(cfg2.resolved_stop_children(), 3)


class TestCreateAgentSession(unittest.TestCase):
    def test_factory_accepts_one_child_with_fake_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            agent_cfg = AgentConfig(
                max_iterations=1,
                stop_children=1,
                prompt=PromptConfig(use_template_stochasticity=False),
                database=DatabaseConfig(in_memory=True, num_islands=1, population_size=20),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30),
                budget=BudgetConfig(total_tokens=1_000_000),
                mutation_cli=MutationCliConfig(kind="opencode"),
            )
            session = create_agent_session(
                agent_cfg,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "out"),
                coordination=NullCoordination(),
                mutation_backend=FakeMutationBackend(
                    producer=lambda _req: _scaffold("def f():\n    return 7\n")
                ),
            )

            import asyncio

            async def scenario():
                await session.begin_run()
                session.next_target()
                session.select_parent()
                await session.get_brief()
                return await session.submit_child(_scaffold("def f():\n    return 7\n"))

            result = asyncio.run(scenario())
            self.assertEqual(result["status"], "accepted")
