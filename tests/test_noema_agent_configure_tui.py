"""Configure TUI apply helpers (module closed field, path resolve, null coerce)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noema.agenthost.config import AgentConfig
from noema.agenthost.configure_files import ExamplePaths
from noema.agenthost.configure_tui import (
    _agent_sections,
    _apply_walk_to_config,
    _finish_escape,
    _resolve_user_path,
)
from noema.agenthost.configure_walk import ConfigureWalk
from noema.config import NoemaConfig
from noema.coordination import MODULE_REGISTRY


class TestFinishEscape(unittest.TestCase):
    def test_csi_arrow_up(self):
        buf = list("[A")

        def read_char() -> str:
            return buf.pop(0)

        self.assertEqual(
            _finish_escape(
                "\x1b",
                read_char=read_char,
                ready=lambda: bool(buf),
            ),
            "\x1b[A",
        )
        self.assertEqual(buf, [])

    def test_bare_esc_when_nothing_follows(self):
        self.assertEqual(
            _finish_escape(
                "\x1b",
                read_char=lambda: "",
                ready=lambda: False,
            ),
            "\x1b",
        )

    def test_ss3_arrow_left(self):
        buf = list("OD")

        def read_char() -> str:
            return buf.pop(0)

        self.assertEqual(
            _finish_escape(
                "\x1b",
                read_char=read_char,
                ready=lambda: bool(buf),
            ),
            "\x1bOD",
        )


class TestResolveUserPath(unittest.TestCase):
    def test_relative_appends_to_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            got = _resolve_user_path("example_output", base)
            self.assertEqual(got, (base / "example_output").resolve())

    def test_absolute_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            abs_out = Path(tmp) / "abs_out"
            got = _resolve_user_path(str(abs_out), Path("/somewhere/else"))
            self.assertEqual(got, abs_out.resolve())


class TestModuleClosedField(unittest.TestCase):
    def test_module_is_closed_with_registry_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prog = root / "initial_program.py"
            ev = root / "evaluator.py"
            prog.write_text("x = 1\n")
            ev.write_text("def evaluate(p): return 0\n")
            paths = ExamplePaths(
                cwd=root,
                initial_program=prog,
                evaluator=ev,
                preferred_config=None,
                config_candidates=(),
            )
            sections = _agent_sections(AgentConfig(), paths, root / "out")
            mod = sections["coordination"][0]
            self.assertEqual(mod["kind"], "closed")
            self.assertEqual(mod["choices"], sorted(MODULE_REGISTRY))
            self.assertEqual(mod["value"], "null")
            self.assertNotIn("llm", sections)
            host_log = next(
                field for field in sections["agent"] if field["id"] == "host_log_verbosity"
            )
            self.assertEqual(host_log["choices"], ["standard"])
            self.assertEqual(host_log["value"], "standard")

    def test_apply_never_writes_python_none_for_module(self):
        walk = ConfigureWalk(
            sections={
                "paths": [
                    {"id": "config", "value": "config.yaml"},
                    {"id": "programme", "value": "/tmp/example/initial_program.py"},
                    {"id": "evaluator", "value": "/tmp/example/evaluator.py"},
                    {"id": "output", "value": "example_output"},
                ],
                "agent": [
                    {"id": "mutation_cli.kind", "value": "claude"},
                    {"id": "mutation_depth", "value": "shallow"},
                    {"id": "coordination_depth", "value": "shallow"},
                    {"id": "host_log_verbosity", "value": "standard"},
                    {"id": "stop_children", "value": ""},
                    {"id": "mutation_cli.model", "value": ""},
                ],
                "coordination": [{"id": "module", "value": None}],
                "advanced": [
                    {"id": "max_iterations", "value": "10"},
                    {"id": "diff_based_evolution", "value": "true"},
                ],
            }
        )
        config, _, output_dir = _apply_walk_to_config(walk, AgentConfig())
        self.assertEqual(config.noema.coordination.module, "null")
        self.assertEqual(config.host_log_verbosity, "standard")
        self.assertEqual(output_dir, Path("/tmp/example/example_output").resolve())


class TestYamlNullModuleCoerce(unittest.TestCase):
    def test_yaml_null_module_becomes_string_null(self):
        cfg = NoemaConfig.from_dict({"coordination": {"module": None}})
        self.assertEqual(cfg.coordination.module, "null")
        self.assertIsInstance(cfg.coordination.module, str)


if __name__ == "__main__":
    unittest.main()
