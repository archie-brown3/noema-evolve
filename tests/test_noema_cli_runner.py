"""Unit tests for noema.budget.cli_runner transport primitive."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from noema.budget.cli_runner import CliRunner


class TestCliRunner(unittest.TestCase):
    def test_run_writes_stdout_stderr_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            stdout_path = work / "out.log"
            stderr_path = work / "err.log"
            completed = MagicMock()
            completed.returncode = 0
            completed.stdout = "hello stdout"
            completed.stderr = "hello stderr"

            with patch("noema.budget.cli_runner.subprocess.run", return_value=completed) as run:
                result = CliRunner().run(
                    ["echo", "ok"],
                    cwd=work,
                    env={"PATH": "/usr/bin"},
                    timeout_s=30.0,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )

            run.assert_called_once()
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            self.assertEqual(stdout_path.read_text(), "hello stdout")
            self.assertEqual(stderr_path.read_text(), "hello stderr")

    def test_run_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            stdout_path = work / "out.log"
            stderr_path = work / "err.log"
            completed = MagicMock()
            completed.returncode = 2
            completed.stdout = ""
            completed.stderr = "failed"

            with patch("noema.budget.cli_runner.subprocess.run", return_value=completed):
                result = CliRunner().run(
                    ["false"],
                    cwd=work,
                    env={},
                    timeout_s=10.0,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )

            self.assertEqual(result.exit_code, 2)
            self.assertFalse(result.timed_out)
            self.assertEqual(stderr_path.read_text(), "failed")

    def test_run_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            stdout_path = work / "out.log"
            stderr_path = work / "err.log"
            exc = subprocess.TimeoutExpired(cmd=["sleep"], timeout=1)
            exc.stdout = b"partial"
            exc.stderr = b"timeout stderr"

            with patch(
                "noema.budget.cli_runner.subprocess.run",
                side_effect=exc,
            ):
                result = CliRunner().run(
                    ["sleep", "99"],
                    cwd=work,
                    env={},
                    timeout_s=1.0,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )

            self.assertTrue(result.timed_out)
            self.assertIsNone(result.exit_code)
            self.assertIn("partial", stdout_path.read_text())
            self.assertIn("timeout stderr", stderr_path.read_text())


if __name__ == "__main__":
    unittest.main()
