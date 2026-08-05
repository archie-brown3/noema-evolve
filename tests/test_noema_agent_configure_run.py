"""Write/run-gate tests for configure CLI (task 0189 / Phase 4)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noema.agenthost.config import AgentCliConfig, AgentConfig
from noema.agenthost.configure_files import load_noema_and_agent
from noema.config import NoemaConfig


class TestCommitAndMaybeRun(unittest.TestCase):
    def test_commit_without_run_writes_yaml_only(self):
        from noema.agenthost.configure import commit_and_maybe_run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            prog = root / "initial_program.py"
            ev = root / "evaluator.py"
            out = root / "out"
            prog.write_text("def f():\n    return 1\n")
            ev.write_text("def evaluate(p):\n    return {'combined_score': 1.0}\n")
            cfg = AgentConfig(
                noema=NoemaConfig(max_iterations=3),
                mutation_cli=AgentCliConfig(kind="claude"),
                mutation_depth="deep",
            )
            result = commit_and_maybe_run(
                config_path=config_path,
                agent_config=cfg,
                evaluation_file=ev,
                initial_program=prog,
                output_dir=out,
                run=False,
            )
            self.assertFalse(result["ran"])
            self.assertTrue(config_path.is_file())
            loaded = load_noema_and_agent(config_path)
            self.assertEqual(loaded.mutation_depth, "deep")
            self.assertEqual(loaded.mutation_cli.kind, "claude")
            self.assertFalse(out.exists())

    def test_commit_with_run_calls_runner(self):
        from noema.agenthost.configure import commit_and_maybe_run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            prog = root / "initial_program.py"
            ev = root / "evaluator.py"
            out = root / "out"
            prog.write_text("def f():\n    return 1\n")
            ev.write_text("def evaluate(p):\n    return {'combined_score': 1.0}\n")
            cfg = AgentConfig(noema=NoemaConfig(max_iterations=2), stop_children=1)
            seen = {}

            def runner(agent_config, evaluation_file, initial_program, output_dir):
                seen["config"] = agent_config
                seen["eval"] = evaluation_file
                seen["initial"] = initial_program
                seen["out"] = output_dir
                return {"stopped": True}

            result = commit_and_maybe_run(
                config_path=config_path,
                agent_config=cfg,
                evaluation_file=ev,
                initial_program=prog,
                output_dir=out,
                run=True,
                runner=runner,
            )
            self.assertTrue(result["ran"])
            self.assertEqual(result["status"], {"stopped": True})
            self.assertTrue(config_path.is_file())
            self.assertIs(seen["config"], cfg)
            self.assertEqual(Path(seen["eval"]), ev.resolve())


class TestDerivedFileSuffixReachesSession(unittest.TestCase):
    """`noema` in a C++ example must evolve child.cpp, not child.py."""

    def _example(self, root: Path, suffix: str) -> None:
        (root / f"initial_program{suffix}").write_text("// seed\n")
        (root / "evaluator.py").write_text("def evaluate(p):\n    return {'combined_score': 1.0}\n")

    def _write_config(self, root: Path) -> Path:
        import os

        from noema.agenthost.configure import main

        cwd = os.getcwd()
        os.chdir(root)
        try:
            self.assertEqual(main(["--write-only"]), 0)
        finally:
            os.chdir(cwd)
        return root / "config.yaml"

    def test_cpp_example_writes_and_runs_with_cpp_suffix(self):
        import warnings

        from noema.agenthost.factory import create_agent_session
        from noema.agenthost.mutation import mutation_layout

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._example(root, ".cpp")
            config_path = self._write_config(root)

            loaded = load_noema_and_agent(config_path)
            self.assertEqual(loaded.noema.file_suffix, ".cpp")

            loaded.noema.llm.mutation.api_key = "fake-key"
            loaded.noema.llm.coordination.api_key = "fake-key"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                session = create_agent_session(
                    loaded,
                    evaluation_file=str(root / "evaluator.py"),
                    initial_program_code="// seed\n",
                    output_dir=str(root / "out"),
                )
            layout = mutation_layout(
                session.output_dir, 0, 1, file_suffix=session.config.file_suffix
            )
            self.assertEqual(layout.deliverable_path.name, "child.cpp")
            self.assertEqual(layout.parent_path.name, "parent.cpp")

    def test_python_example_keeps_py_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._example(root, ".py")
            loaded = load_noema_and_agent(self._write_config(root))
            self.assertEqual(loaded.noema.file_suffix, ".py")

    def test_explicit_yaml_suffix_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._example(root, ".cpp")
            (root / "config.yaml").write_text(
                "file_suffix: .cc\nprompt:\n  use_template_stochasticity: false\n"
            )
            loaded = load_noema_and_agent(self._write_config(root))
            self.assertEqual(loaded.noema.file_suffix, ".cc")


if __name__ == "__main__":
    unittest.main()
