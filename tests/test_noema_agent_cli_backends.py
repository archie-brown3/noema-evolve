"""Unit tests for headless mutation CLI adapters (claude / codex / opencode)."""

import tempfile
import unittest
from pathlib import Path

from noema.agenthost.cli_backends import (
    SUPPORTED_MUTATION_CLIS,
    build_cli_user_message,
    build_mutation_cli_command,
    deliverable_envelope,
)
from noema.agenthost.materialize import materialize_child_code


class TestCliCommandBuilders(unittest.TestCase):
    def test_supported_kinds(self):
        self.assertEqual(
            SUPPORTED_MUTATION_CLIS,
            ("claude", "codex", "opencode", "agent"),
        )

    def test_claude_argv_uses_print_and_system_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            system = work / "SYSTEM.md"
            system.write_text("sys")
            cmd = build_mutation_cli_command(
                "claude",
                work_dir=work,
                system_path=system,
                user_message="improve child.py",
                binary="/usr/bin/claude",
            )
            self.assertEqual(cmd[0], "/usr/bin/claude")
            self.assertIn("-p", cmd)
            self.assertIn("--system-prompt-file", cmd)
            self.assertIn(str(system), cmd)
            self.assertIn("--dangerously-skip-permissions", cmd)
            self.assertEqual(cmd[-1], "improve child.py")

    def test_codex_argv_uses_exec_and_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            system = work / "SYSTEM.md"
            system.write_text("sys-block")
            cmd = build_mutation_cli_command(
                "codex",
                work_dir=work,
                system_path=system,
                user_message="do the mutation",
                binary="/usr/bin/codex",
                model="o3",
            )
            self.assertEqual(cmd[:2], ["/usr/bin/codex", "exec"])
            self.assertIn("-C", cmd)
            self.assertIn(str(work), cmd)
            self.assertIn("--skip-git-repo-check", cmd)
            self.assertIn("workspace-write", cmd)
            self.assertIn("-m", cmd)
            self.assertIn("o3", cmd)
            self.assertIn("sys-block", cmd[-1])
            self.assertIn("do the mutation", cmd[-1])

    def test_opencode_argv_uses_run_auto_and_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            system = work / "SYSTEM.md"
            system.write_text("sys")
            cmd = build_mutation_cli_command(
                "opencode",
                work_dir=work,
                system_path=system,
                user_message="mutate",
                binary="/usr/bin/opencode",
            )
            self.assertEqual(cmd[:2], ["/usr/bin/opencode", "run"])
            self.assertIn("--dir", cmd)
            self.assertIn("--auto", cmd)
            self.assertIn("--file", cmd)
            self.assertIn("--", cmd)
            self.assertEqual(cmd[-1], "mutate")

    def test_agent_argv_uses_print_and_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            system = work / "SYSTEM.md"
            system.write_text("sys-block")
            cmd = build_mutation_cli_command(
                "agent",
                work_dir=work,
                system_path=system,
                user_message="do the mutation",
                binary="/usr/bin/agent",
                model="sonnet-4",
            )
            self.assertEqual(cmd[0], "/usr/bin/agent")
            self.assertIn("-p", cmd)
            self.assertIn("--trust", cmd)
            self.assertIn("--force", cmd)
            self.assertIn("--workspace", cmd)
            self.assertIn(str(work), cmd)
            self.assertIn("--model", cmd)
            self.assertIn("sonnet-4", cmd)
            self.assertIn("sys-block", cmd[-1])
            self.assertIn("do the mutation", cmd[-1])

    def test_envelope_names_absolute_deliverable(self):
        text = deliverable_envelope(
            deliverable=Path("/tmp/run/child.py"),
            parent_path=Path("/tmp/run/parent.py"),
        )
        self.assertIn("/tmp/run/child.py", text)
        msg = build_cli_user_message(
            {"user": "Noema user prompt"},
            deliverable=Path("/tmp/run/child.py"),
            parent_path=Path("/tmp/run/parent.py"),
        )
        self.assertTrue(msg.startswith("Noema user prompt"))
        self.assertIn("/tmp/run/child.py", msg)


class TestMaterializeChildCode(unittest.TestCase):
    PARENT = "def f():\n    return 1\n"
    DIFF_PATTERN = r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE"

    def test_full_rewrite_accepts_fenced_or_raw(self):
        raw = "def f():\n    return 9\n"
        self.assertEqual(
            materialize_child_code(
                raw,
                self.PARENT,
                parse_mode="full_rewrite",
                language="python",
                diff_pattern=self.DIFF_PATTERN,
            ),
            raw,
        )

    def test_diff_mode_applies_search_replace(self):
        response = (
            "<<<<<<< SEARCH\n"
            "def f():\n    return 1\n"
            "=======\n"
            "def f():\n    return 4\n"
            ">>>>>>> REPLACE\n"
        )
        child = materialize_child_code(
            response,
            self.PARENT,
            parse_mode="diff",
            language="python",
            diff_pattern=self.DIFF_PATTERN,
        )
        self.assertIn("return 4", child)

    def test_diff_mode_falls_back_to_full_file(self):
        child = materialize_child_code(
            "def f():\n    return 5\n",
            self.PARENT,
            parse_mode="diff",
            language="python",
            diff_pattern=self.DIFF_PATTERN,
        )
        self.assertIn("return 5", child)


if __name__ == "__main__":
    unittest.main()
