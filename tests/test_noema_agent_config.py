"""Canonical config composition and agent-host factory tests."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.agenthost.cli_bootstrap import build_agent_config, parse_entry_args
from noema.agenthost.config import AgentCliConfig, AgentConfig, validate_agent_config
from noema.agenthost.factory import create_agent_session
from noema.agenthost.inner_session_mcp import MCP_CONFIG_NAME, TOOLS_DIRNAME
from noema.agenthost.mutation import CliMutationBackend, FakeMutationBackend
from noema.agenthost.reasoning import DeepCoordinationLLM
from noema.agenthost.submit import ADVICE_FILENAME
from noema.budget.cli_runner import CliPtyRunner, CliRunResult
from noema.budget.llm import BudgetedLLM
from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    LLMClientConfig,
    LLMRolesConfig,
    NoemaConfig,
)
from noema.coordination import NullCoordination
from tests.test_noema_agent_arm_sweep import EVAL_SCRIPT, INITIAL_PROGRAM, _scaffold


class TestAgentConfig(unittest.TestCase):
    def test_valid_config_passes(self):
        validate_agent_config(AgentConfig())

    def test_bad_mutation_depth_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(mutation_depth="invalid")  # type: ignore[arg-type]

    def test_bad_coordination_depth_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(coordination_depth="invalid")  # type: ignore[arg-type]

    def test_bad_host_log_verbosity_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(host_log_verbosity="invalid")  # type: ignore[arg-type]

    def test_bad_active_mutation_cli_kind_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(mutation_cli=AgentCliConfig(kind="invalid"))

    def test_bad_active_coordination_cli_kind_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig(
                coordination_depth="deep",
                coordination_cli=AgentCliConfig(kind="invalid"),
            )

    def test_inactive_coordination_cli_is_warning_not_error(self):
        config = AgentConfig(coordination_cli=AgentCliConfig(kind="invalid"))
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            session = self._session(tmp, config, coordination=NullCoordination())
        self.assertIsNotNone(session)
        self.assertTrue(any("coordination_cli is ignored" in str(item.message) for item in seen))

    def test_stop_children_overlay_does_not_mutate_canonical_limit(self):
        noema = NoemaConfig(max_iterations=10)
        config = AgentConfig(noema=noema, stop_children=3)
        self.assertEqual(config.resolved_stop_children(), 3)
        self.assertEqual(config.noema.max_iterations, 10)

    def test_cli_bootstrap_constructs_complete_overlay_once(self):
        args = parse_entry_args(
            [
                "--evaluation-file",
                "/tmp/eval.py",
                "--initial-program",
                "/tmp/init.py",
                "--output-dir",
                "/tmp/out",
                "--stop-children",
                "3",
                "--mutation-cli",
                "codex",
                "--mutation-model",
                "strong-model",
            ]
        )
        config = build_agent_config(args)
        self.assertEqual(config.mutation_depth, "shallow")
        self.assertEqual(config.coordination_depth, "shallow")
        self.assertEqual(config.mutation_cli.kind, "codex")
        self.assertEqual(config.mutation_cli.model, "strong-model")
        self.assertEqual(config.resolved_stop_children(), 3)
        self.assertEqual(config.noema.max_iterations, NoemaConfig().max_iterations)

    def test_cli_bootstrap_preserves_canonical_yaml(self):
        canonical = NoemaConfig(
            max_iterations=12,
            random_seed=19,
            coordination=CoordinationConfig(module="hifo"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(canonical.to_yaml())
            args = parse_entry_args(
                [
                    "--config",
                    str(config_path),
                    "--evaluation-file",
                    "/tmp/eval.py",
                    "--initial-program",
                    "/tmp/init.py",
                    "--output-dir",
                    "/tmp/out",
                    "--stop-children",
                    "3",
                ]
            )
            config = build_agent_config(args)

        self.assertEqual(config.noema.to_dict(), canonical.to_dict())
        self.assertEqual(config.resolved_stop_children(), 3)

    def test_cli_bootstrap_reads_agent_block_from_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "max_iterations: 11",
                        "prompt:",
                        "  use_template_stochasticity: false",
                        "agent:",
                        "  mutation_depth: deep",
                        "  mutation_cli:",
                        "    kind: claude",
                        "    model: sonnet",
                        "",
                    ]
                )
            )
            args = parse_entry_args(
                [
                    "--config",
                    str(config_path),
                    "--evaluation-file",
                    "/tmp/eval.py",
                    "--initial-program",
                    "/tmp/init.py",
                    "--output-dir",
                    "/tmp/out",
                ]
            )
            config = build_agent_config(args)
        self.assertEqual(config.noema.max_iterations, 11)
        self.assertEqual(config.mutation_depth, "deep")
        self.assertEqual(config.mutation_cli.kind, "claude")
        self.assertEqual(config.mutation_cli.model, "sonnet")

    def test_cli_bootstrap_loads_deep_coordination_cli_from_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "max_iterations: 5",
                        "prompt:",
                        "  use_template_stochasticity: false",
                        "agent:",
                        "  coordination_depth: deep",
                        "  mutation_cli:",
                        "    kind: opencode",
                        "  coordination_cli:",
                        "    kind: claude",
                        "    model: coord-model",
                        "",
                    ]
                )
            )
            args = parse_entry_args(
                [
                    "--config",
                    str(config_path),
                    "--evaluation-file",
                    "/tmp/eval.py",
                    "--initial-program",
                    "/tmp/init.py",
                    "--output-dir",
                    "/tmp/out",
                ]
            )
            config = build_agent_config(args)
        self.assertEqual(config.coordination_depth, "deep")
        self.assertEqual(config.coordination_cli.kind, "claude")
        self.assertEqual(config.coordination_cli.model, "coord-model")

    def test_cli_bootstrap_flag_deep_clones_mutation_cli(self):
        args = parse_entry_args(
            [
                "--evaluation-file",
                "/tmp/eval.py",
                "--initial-program",
                "/tmp/init.py",
                "--output-dir",
                "/tmp/out",
                "--mutation-cli",
                "codex",
                "--mutation-model",
                "m1",
                "--coordination-depth",
                "deep",
            ]
        )
        config = build_agent_config(args)
        self.assertEqual(config.coordination_depth, "deep")
        self.assertEqual(config.coordination_cli.kind, "codex")
        self.assertEqual(config.coordination_cli.model, "m1")

    def test_factory_passes_canonical_config_without_projection(self):
        noema = NoemaConfig(
            max_iterations=5,
            coordination=CoordinationConfig(module="null"),
        )
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(
                tmp,
                AgentConfig(noema=noema),
                coordination=NullCoordination(),
            )
        self.assertIs(session.config, noema)

    def test_factory_emits_transport_difference_warnings(self):
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self._session(tmp, AgentConfig(), coordination=NullCoordination())
        messages = [str(item.message) for item in seen]
        self.assertTrue(any("llm.mutation is ignored" in message for message in messages))
        self.assertTrue(any("cannot meter" in message for message in messages))
        self.assertTrue(any("automatic checkpoints" in message for message in messages))

    def _session(self, tmp, config, **kwargs):
        eval_path = os.path.join(tmp, "evaluator.py")
        with open(eval_path, "w") as handle:
            handle.write(EVAL_SCRIPT)
        return create_agent_session(
            config,
            evaluation_file=eval_path,
            initial_program_code=INITIAL_PROGRAM,
            output_dir=os.path.join(tmp, "out"),
            **kwargs,
        )


class TestCreateAgentSession(unittest.TestCase):
    def _session(self, tmp, config, **kwargs):
        eval_path = os.path.join(tmp, "evaluator.py")
        with open(eval_path, "w") as handle:
            handle.write(EVAL_SCRIPT)
        return create_agent_session(
            config,
            evaluation_file=eval_path,
            initial_program_code=INITIAL_PROGRAM,
            output_dir=os.path.join(tmp, "out"),
            **kwargs,
        )

    def test_factory_shallow_coordination_uses_budgeted_llm(self):
        noema = NoemaConfig(
            coordination=CoordinationConfig(module="hifo"),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(api_key="fake-key"),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(tmp, AgentConfig(noema=noema))
        self.assertIsInstance(session.coordination.llm, BudgetedLLM)
        self.assertNotIsInstance(session.coordination.llm, DeepCoordinationLLM)

    def test_factory_deep_coordination_wraps_llm(self):
        noema = NoemaConfig(
            coordination=CoordinationConfig(module="hifo"),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(api_key="fake-key"),
            ),
        )
        config = AgentConfig(
            noema=noema,
            coordination_depth="deep",
            coordination_cli=AgentCliConfig(kind="opencode"),
        )
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(tmp, config)
        self.assertIsInstance(session.coordination.llm, DeepCoordinationLLM)

    def test_factory_bootstraps_escalation_model_from_coordination_seat(self):
        noema = NoemaConfig(
            coordination=CoordinationConfig(module="bandit"),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(model="coord-model", api_key="fake-key"),
            ),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            warnings.catch_warnings(),
            mock.patch("openai.AsyncOpenAI"),
        ):
            warnings.simplefilter("ignore")
            session = self._session(tmp, AgentConfig(noema=noema))
        self.assertIs(session.config, noema)
        self.assertEqual(session.coordination.config["escalation_model"], "coord-model")

    def test_factory_wires_pe_alternate_tier_models(self):
        noema = NoemaConfig(
            coordination=CoordinationConfig(
                module="pe",
                params={
                    "paradigm_model": "heavy-model",
                    "variant_model": "light-model",
                },
            ),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(model="base-model", api_key="fake-key"),
            ),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            warnings.catch_warnings(),
            mock.patch("openai.AsyncOpenAI"),
        ):
            warnings.simplefilter("ignore")
            session = self._session(tmp, AgentConfig(noema=noema))
        self.assertIs(session.config, noema)
        self.assertEqual(session.coordination._paradigm_llm.model, "heavy-model")
        self.assertEqual(session.coordination._variant_llm.model, "light-model")
        self.assertEqual(session.coordination.llm.model, "base-model")

    def test_factory_deep_pe_alternate_tiers_use_bound_cli_transport(self):
        noema = NoemaConfig(
            coordination=CoordinationConfig(
                module="pe",
                params={
                    "paradigm_model": "heavy-model",
                    "variant_model": "light-model",
                },
            ),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(model="base-model", api_key="fake-key"),
            ),
        )
        config = AgentConfig(
            noema=noema,
            coordination_depth="deep",
            coordination_cli=AgentCliConfig(kind="opencode", binary=sys.executable),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            warnings.catch_warnings(),
            mock.patch("openai.AsyncOpenAI"),
        ):
            warnings.simplefilter("ignore")
            session = self._session(tmp, config)

            self.assertIsInstance(session.coordination.llm, DeepCoordinationLLM)
            self.assertIsInstance(session.coordination._paradigm_llm, DeepCoordinationLLM)
            self.assertIsInstance(session.coordination._variant_llm, DeepCoordinationLLM)
            self.assertIs(session.coordination.llm._session, session)
            self.assertIs(session.coordination._paradigm_llm._session, session)
            self.assertIs(session.coordination._variant_llm._session, session)
            self.assertEqual(session.coordination._paradigm_llm.model, "heavy-model")
            self.assertEqual(session.coordination._variant_llm.model, "light-model")

            captured = {}

            def fake_run(_self, argv, **kwargs):
                captured["argv"] = argv
                captured["cwd"] = kwargs["cwd"]
                kwargs["stdout_path"].write_text("")
                kwargs["stderr_path"].write_text("")
                (kwargs["cwd"] / ADVICE_FILENAME).write_text(json.dumps({"response": "ok"}))
                return CliRunResult(exit_code=0, stdout="", stderr="", wall_s=0.0, timed_out=False)

            with mock.patch.object(CliPtyRunner, "run", fake_run):
                result = asyncio.run(
                    session.coordination._paradigm_llm.generate("prompt", tag="pe.paradigm_shift")
                )

            self.assertEqual(result, "ok")
            self.assertIn("-m", captured["argv"])
            self.assertIn("heavy-model", captured["argv"])
            self.assertTrue((captured["cwd"] / TOOLS_DIRNAME / MCP_CONFIG_NAME).is_file())
            opencode = json.loads((captured["cwd"] / "opencode.json").read_text())
            self.assertTrue(opencode["mcp"]["noema"]["enabled"])

    def test_deep_coordination_cli_model_overrides_wrapped_seat_model(self):
        noema = NoemaConfig(
            coordination=CoordinationConfig(
                module="pe",
                params={"paradigm_model": "heavy-model"},
            ),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(model="base-model", api_key="fake-key"),
            ),
        )
        config = AgentConfig(
            noema=noema,
            coordination_depth="deep",
            coordination_cli=AgentCliConfig(
                kind="opencode",
                binary=sys.executable,
                model="cli-model",
            ),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            warnings.catch_warnings(),
            mock.patch("openai.AsyncOpenAI"),
        ):
            warnings.simplefilter("ignore")
            session = self._session(tmp, config)
            captured = {}

            def fake_run(_self, argv, **kwargs):
                captured["argv"] = argv
                kwargs["stdout_path"].write_text("")
                kwargs["stderr_path"].write_text("")
                (kwargs["cwd"] / ADVICE_FILENAME).write_text(json.dumps({"response": "ok"}))
                return CliRunResult(exit_code=0, stdout="", stderr="", wall_s=0.0, timed_out=False)

            with mock.patch.object(CliPtyRunner, "run", fake_run):
                result = asyncio.run(
                    session.coordination._paradigm_llm.generate("prompt", tag="pe.paradigm_shift")
                )

        self.assertEqual(result, "ok")
        self.assertIn("cli-model", captured["argv"])
        self.assertNotIn("heavy-model", captured["argv"])

    def test_shallow_mutation_builds_unbound_cli_backend(self):
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(
                tmp,
                AgentConfig(mutation_depth="shallow"),
                coordination=NullCoordination(),
            )
        self.assertIsInstance(session.mutation_backend, CliMutationBackend)
        self.assertIsNone(session.mutation_backend._session)

    def test_deep_mutation_builds_bound_cli_backend(self):
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(
                tmp,
                AgentConfig(mutation_depth="deep"),
                coordination=NullCoordination(),
            )
        self.assertIsInstance(session.mutation_backend, CliMutationBackend)
        self.assertIs(session.mutation_backend._session, session)

    def test_factory_accepts_one_child_with_fake_backend(self):
        noema = NoemaConfig(
            max_iterations=1,
            database=DatabaseConfig(
                in_memory=True,
                num_islands=1,
                population_size=20,
            ),
            evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30),
            budget=BudgetConfig(total_tokens=1_000_000),
        )
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(
                tmp,
                AgentConfig(noema=noema, stop_children=1),
                coordination=NullCoordination(),
                mutation_backend=FakeMutationBackend(
                    producer=lambda _request: _scaffold("def f():\n    return 7\n")
                ),
            )

            import asyncio

            status = asyncio.run(session.run_agent_mode())

        self.assertEqual(status["children_accepted"], 1)
        self.assertTrue(status["stopped"])

    def test_mutation_cli_timeout_reaches_each_request(self):
        requests = []
        noema = NoemaConfig(max_iterations=1)
        config = AgentConfig(
            noema=noema,
            stop_children=1,
            mutation_cli=AgentCliConfig(timeout_s=7.5),
        )
        backend = FakeMutationBackend(
            producer=lambda request: (
                requests.append(request) or _scaffold("def f():\n    return 7\n")
            )
        )
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            session = self._session(
                tmp,
                config,
                coordination=NullCoordination(),
                mutation_backend=backend,
            )

            import asyncio

            asyncio.run(session.run_agent_mode())

        self.assertEqual([request.timeout_s for request in requests], [7.5])


if __name__ == "__main__":
    unittest.main()
