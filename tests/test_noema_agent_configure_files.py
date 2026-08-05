"""Configure CLI file-seam tests (task 0189 / Phase 1)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noema.agenthost.configure_files import discover_example


def _write_minimal_example(cwd: Path, *, configs: list[str] | None = None) -> None:
    (cwd / "initial_program.py").write_text("def f():\n    return 1\n")
    (cwd / "evaluator.py").write_text("def evaluate(p):\n    return {'combined_score': 1.0}\n")
    for name in configs or []:
        # Minimal Noema-shaped YAML (stochasticity off)
        (cwd / name).write_text("max_iterations: 1\nprompt:\n  use_template_stochasticity: false\n")


def _write_openevolve_yaml(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "max_iterations: 10",
                "log_level: INFO",
                "llm:",
                "  primary_model: google/gemini-2.0-flash-001",
                "  api_base: https://openrouter.ai/api/v1",
                "prompt:",
                "  use_template_stochasticity: true",
                "",
            ]
        )
    )


class TestDiscoverExample(unittest.TestCase):
    def test_missing_programme_and_evaluator_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            with self.assertRaises(ValueError) as ctx:
                discover_example(cwd)
            msg = str(ctx.exception).lower()
            self.assertIn("initial_program", msg)
            self.assertIn("evaluator.py", msg)

    def test_prefers_config_yaml_then_noema_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            _write_minimal_example(cwd, configs=["other.yaml", "noema.yaml", "config.yaml"])
            found = discover_example(cwd)
            self.assertEqual(found.preferred_config, (cwd / "config.yaml").resolve())
            self.assertIn((cwd / "config.yaml").resolve(), found.config_candidates)
            self.assertIn((cwd / "noema.yaml").resolve(), found.config_candidates)
            self.assertIn((cwd / "other.yaml").resolve(), found.config_candidates)

    def test_includes_openevolve_shaped_yaml_in_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            _write_minimal_example(cwd, configs=["noema.yaml"])
            _write_openevolve_yaml(cwd / "config_phase_1.yaml")
            found = discover_example(cwd)
            names = {p.name for p in found.config_candidates}
            self.assertEqual(names, {"noema.yaml", "config_phase_1.yaml"})
            self.assertEqual(found.preferred_config, (cwd / "noema.yaml").resolve())


class TestDiscoverInitialProgramLanguage(unittest.TestCase):
    """The seed programme may be in any language; only the evaluator is Python."""

    def _example(self, cwd: Path, *names: str) -> None:
        (cwd / "evaluator.py").write_text("def evaluate(p):\n    return {'combined_score': 1.0}\n")
        for name in names:
            (cwd / name).write_text("// seed\n")

    def test_non_python_initial_program_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            self._example(cwd, "initial_program.cpp")
            found = discover_example(cwd)
            self.assertEqual(found.initial_program, (cwd / "initial_program.cpp").resolve())
            self.assertEqual(found.file_suffix, ".cpp")

    def test_python_initial_program_still_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            self._example(cwd, "initial_program.py")
            found = discover_example(cwd)
            self.assertEqual(found.initial_program, (cwd / "initial_program.py").resolve())
            self.assertEqual(found.file_suffix, ".py")

    def test_ambiguous_initial_programs_rejected_listing_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            self._example(cwd, "initial_program.cpp", "initial_program.py")
            with self.assertRaises(ValueError) as ctx:
                discover_example(cwd)
            msg = str(ctx.exception)
            self.assertIn("initial_program.cpp", msg)
            self.assertIn("initial_program.py", msg)

    def test_evaluator_must_still_be_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / "initial_program.cpp").write_text("// seed\n")
            (cwd / "evaluator.cpp").write_text("// not a python evaluator\n")
            with self.assertRaises(ValueError) as ctx:
                discover_example(cwd)
            self.assertIn("evaluator.py", str(ctx.exception))


class TestDiscoverSkeleton(unittest.TestCase):
    def test_no_yaml_candidates_signal_create_new_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            _write_minimal_example(cwd, configs=None)
            found = discover_example(cwd)
            self.assertEqual(found.config_candidates, ())
            self.assertIsNone(found.preferred_config)
            self.assertTrue(found.use_skeleton)
            self.assertEqual(found.new_config_path, (cwd / "config.yaml").resolve())


class TestLoadNoemaAndAgent(unittest.TestCase):
    def test_load_strips_agent_block_into_agent_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "\n".join(
                    [
                        "max_iterations: 7",
                        "prompt:",
                        "  use_template_stochasticity: false",
                        "agent:",
                        "  stop_children: 3",
                        "  mutation_depth: deep",
                        "  coordination_depth: shallow",
                        "  host_log_verbosity: full",
                        "  mutation_cli:",
                        "    kind: claude",
                        "    model: sonnet",
                        "    timeout_s: 120",
                        "",
                    ]
                )
            )
            from noema.agenthost.configure_files import load_noema_and_agent

            agent_cfg = load_noema_and_agent(path)
            self.assertEqual(agent_cfg.noema.max_iterations, 7)
            self.assertEqual(agent_cfg.stop_children, 3)
            self.assertEqual(agent_cfg.mutation_depth, "deep")
            self.assertEqual(agent_cfg.coordination_depth, "shallow")
            self.assertEqual(agent_cfg.host_log_verbosity, "standard")
            self.assertEqual(agent_cfg.mutation_cli.kind, "claude")
            self.assertEqual(agent_cfg.mutation_cli.model, "sonnet")
            self.assertEqual(agent_cfg.mutation_cli.timeout_s, 120.0)

    def test_save_round_trips_agent_block(self):
        from noema.agenthost.config import AgentCliConfig, AgentConfig
        from noema.agenthost.configure_files import load_noema_and_agent, save_noema_and_agent
        from noema.config import NoemaConfig

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            original = AgentConfig(
                noema=NoemaConfig(max_iterations=9),
                stop_children=2,
                mutation_depth="deep",
                coordination_depth="shallow",
                host_log_verbosity="standard",
                mutation_cli=AgentCliConfig(kind="codex", model="gpt-5", timeout_s=90.0),
            )
            save_noema_and_agent(path, original)
            text = path.read_text()
            self.assertIn("agent:", text)
            self.assertIn("mutation_depth: deep", text)
            reloaded = load_noema_and_agent(path)
            self.assertEqual(reloaded.noema.max_iterations, 9)
            self.assertEqual(reloaded.stop_children, 2)
            self.assertEqual(reloaded.mutation_depth, "deep")
            self.assertEqual(reloaded.host_log_verbosity, "standard")
            self.assertIn("host_log_verbosity: standard", text)
            self.assertEqual(reloaded.mutation_cli.kind, "codex")
            self.assertEqual(reloaded.mutation_cli.model, "gpt-5")
            self.assertEqual(reloaded.mutation_cli.timeout_s, 90.0)

    def test_missing_agent_block_uses_transport_defaults(self):
        from noema.agenthost.config import AgentCliConfig, AgentConfig
        from noema.agenthost.configure_files import load_noema_and_agent

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("max_iterations: 4\nprompt:\n  use_template_stochasticity: false\n")
            loaded = load_noema_and_agent(path)
            defaults = AgentConfig()
            self.assertEqual(loaded.noema.max_iterations, 4)
            self.assertIsNone(loaded.stop_children)
            self.assertEqual(loaded.mutation_depth, defaults.mutation_depth)
            self.assertEqual(loaded.coordination_depth, defaults.coordination_depth)
            self.assertEqual(loaded.host_log_verbosity, "standard")
            self.assertEqual(loaded.mutation_cli.kind, defaults.mutation_cli.kind)
            self.assertEqual(loaded.mutation_cli.timeout_s, defaults.mutation_cli.timeout_s)
            self.assertEqual(loaded.coordination_cli, AgentCliConfig())

    def test_legacy_host_log_values_all_load_as_standard(self):
        from noema.agenthost.configure_files import load_noema_and_agent

        for value in ("accepted", "full", "normal", "debug"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.yaml"
                path.write_text(
                    "prompt:\n  use_template_stochasticity: false\n"
                    f"agent:\n  host_log_verbosity: {value}\n"
                )
                loaded = load_noema_and_agent(path)
                self.assertEqual(loaded.host_log_verbosity, "standard")


if __name__ == "__main__":
    unittest.main()
