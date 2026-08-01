"""Deep coordination adapter tests (task 0174)."""

import asyncio
import json
import os
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from noema.agenthost.config import AgentCliConfig, AgentConfig
from noema.agenthost.factory import create_agent_session
from noema.agenthost.reasoning import DeepCoordinationLLM
from noema.budget.cli_runner import CliRunner, CliRunResult
from noema.budget.ledger import COORDINATION_ACCOUNT, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import CoordinationConfig, LLMClientConfig, LLMRolesConfig, NoemaConfig
from noema.coordination.base import GenerationContext, PopulationSnapshot
from noema.coordination.hifo.module import HiFoPromptModule
from noema.evolution.views import ProgramView
from tests.test_noema_agent_arm_sweep import EVAL_SCRIPT, INITIAL_PROGRAM


def make_view(fitness=0.5, code="def f():\n    return 1\n", desc="") -> ProgramView:
    return ProgramView(id="p", code=code, fitness=fitness, changes_description=desc)


def make_hifo_ctx(**overrides) -> GenerationContext:
    top = overrides.pop(
        "top_programs", [make_view(fitness=0.9, desc="greedy scoring"), make_view(), make_view()]
    )
    snap = PopulationSnapshot(
        scope=None,
        top_programs=tuple(top),
        fitnesses=tuple(p.fitness for p in top),
        best_program=top[0],
        topology="cvt_regions",
    )
    defaults = dict(
        iteration=0,
        generation=1,
        scope_id=0,
        parent=make_view(),
        local_population=snap,
        global_population=snap,
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


class TestDeepCoordinationLLM(unittest.TestCase):
    def _inner(self) -> BudgetedLLM:
        return BudgetedLLM(
            model="fake",
            ledger=TokenLedger(total_budget_tokens=100_000),
            account=COORDINATION_ACCOUNT,
            tag="test.coordination",
            client=SimpleNamespace(),
            retries=0,
            retry_delay=0.0,
        )

    def _agent_cfg_hifo_deep(self) -> AgentConfig:
        return AgentConfig(
            noema=NoemaConfig(
                coordination=CoordinationConfig(module="hifo"),
                llm=LLMRolesConfig(
                    coordination=LLMClientConfig(api_key="fake-key"),
                ),
            ),
            coordination_depth="deep",
            coordination_cli=AgentCliConfig(kind="opencode"),
        )

    def test_deep_llm_spawn_records_tags(self):
        calls: list[str] = []

        def spawn(tag: str, _system: str, _user: str) -> str:
            calls.append(tag)
            return "ok"

        llm = DeepCoordinationLLM(
            self._inner(),
            cli=AgentCliConfig(kind="opencode"),
            output_dir=tempfile.mkdtemp(),
            spawn=spawn,
        )
        llm.generation = 2

        async def run():
            await llm.generate("plan prompt", tag="pes.plan")
            await llm.generate_with_context(
                "",
                [{"role": "user", "content": "extract"}],
                tag="hifo.extract_insights",
            )

        asyncio.run(run())
        self.assertEqual(calls, ["pes.plan", "hifo.extract_insights"])

    def test_factory_deep_wraps_coordination_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            agent_cfg = self._agent_cfg_hifo_deep()
            session = create_agent_session(
                agent_cfg,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "out"),
            )
            self.assertIsInstance(session.coordination.llm, DeepCoordinationLLM)

    def test_hifo_deep_extract_populates_tips(self):
        extraction = (
            "Principles:\n"
            "- Exploit sparse matrix structure to skip redundant work entirely\n"
            "- Cache intermediate scoring results across neighborhood evaluations\n"
        )

        def spawn(tag: str, _system: str, _user: str) -> str:
            if tag == "hifo.extract_insights":
                return extraction
            return ""

        inner = self._inner()
        llm = DeepCoordinationLLM(
            inner,
            cli=AgentCliConfig(kind="opencode"),
            output_dir=tempfile.mkdtemp(),
            spawn=spawn,
        )
        module = HiFoPromptModule(
            config={
                "extraction_probability": 1.0,
                "extraction_interval_offspring": None,
            },
            llm=llm,
            rng=random.Random(0),
        )
        llm.generation = 1
        tips_before = len(module.insight_pool.tips)
        asyncio.run(module.on_generation_end(make_hifo_ctx()))
        self.assertGreater(len(module.insight_pool.tips), tips_before)
        self.assertIn(
            "Exploit sparse matrix structure to skip redundant work entirely",
            module.insight_pool.tips,
        )

    def test_hifo_deep_reads_advice_file(self):
        def fake_run(_self, argv, **kwargs):
            cwd = kwargs["cwd"]
            stdout_path = kwargs["stdout_path"]
            stderr_path = kwargs["stderr_path"]
            advice = {
                "prompt_block": "",
                "attribution": {
                    "insights": [
                        "Exploit sparse matrix structure to skip redundant work entirely",
                        "Cache intermediate scoring results across neighborhood evaluations",
                    ],
                },
            }
            (cwd / "ADVICE.json").write_text(json.dumps(advice))
            stdout_path.write_text("")
            stderr_path.write_text("")
            return CliRunResult(exit_code=0, stdout="", stderr="", wall_s=0.0, timed_out=False)

        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as handle:
                handle.write(EVAL_SCRIPT)
            session = create_agent_session(
                self._agent_cfg_hifo_deep(),
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "out"),
            )
            asyncio.run(session.begin_run())
            llm = session.coordination.llm
            self.assertIsInstance(llm, DeepCoordinationLLM)
            module = HiFoPromptModule(
                config={
                    "extraction_probability": 1.0,
                    "extraction_interval_offspring": None,
                },
                llm=llm,
                rng=random.Random(0),
            )
            llm.generation = 1
            tips_before = len(module.insight_pool.tips)
            with patch.object(CliRunner, "run", fake_run):
                asyncio.run(module.on_generation_end(make_hifo_ctx()))
            self.assertGreater(len(module.insight_pool.tips), tips_before)
            self.assertIn(
                "Exploit sparse matrix structure to skip redundant work entirely",
                module.insight_pool.tips,
            )
