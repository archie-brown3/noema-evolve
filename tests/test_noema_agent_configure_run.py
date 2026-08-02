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


if __name__ == "__main__":
    unittest.main()
