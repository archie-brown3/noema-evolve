"""Unit tests for noema.budget.cli_runner transport primitive."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from noema.budget.cli_runner import CliPtyRunner, CliRunner


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


class TestCliPtyRunner(unittest.TestCase):
    def test_run_streams_merged_terminal_paint_to_callback_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            stdout_path = work / "cli_stdout.log"
            stderr_path = work / "cli_stderr.log"
            chunks = []
            result = CliPtyRunner(on_output=chunks.append).run(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'\\x1b[32mstdout\\x1b[0m\\n'); "
                    "os.write(2, b'stderr\\n')",
                ],
                cwd=work,
                env={},
                timeout_s=5.0,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            self.assertIn("stdout", result.stdout)
            self.assertIn("stderr", result.stdout)
            self.assertIn("\x1b[32m", result.stdout)
            self.assertEqual(result.stderr, "")
            self.assertEqual(stdout_path.read_bytes().decode(), result.stdout)
            self.assertEqual(stderr_path.read_text(), "")
            self.assertIn(b"stdout", b"".join(chunks))

    def test_run_times_out_and_reaps_the_cli_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            result = CliPtyRunner().run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=work,
                env={},
                timeout_s=0.1,
                stdout_path=work / "cli_stdout.log",
                stderr_path=work / "cli_stderr.log",
            )

            self.assertTrue(result.timed_out)
            self.assertIsNone(result.exit_code)

    def test_run_terminates_when_submit_marker_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / "tools" / "mutation_submitted.json"
            script = (
                "import os, time\n"
                "from pathlib import Path\n"
                "p = Path(os.environ['SUBMIT_MARKER'])\n"
                "time.sleep(0.05)\n"
                "p.parent.mkdir(parents=True, exist_ok=True)\n"
                'p.write_text(\'{"status":"submitted"}\')\n'
                "time.sleep(60)\n"
            )
            result = CliPtyRunner().run(
                [sys.executable, "-c", script],
                cwd=work,
                env={"SUBMIT_MARKER": str(marker)},
                timeout_s=5.0,
                stdout_path=work / "cli_stdout.log",
                stderr_path=work / "cli_stderr.log",
                submit_marker_path=marker,
            )

            self.assertTrue(result.submit_received)
            self.assertFalse(result.timed_out)
            self.assertLess(result.wall_s, 2.0)


if __name__ == "__main__":
    unittest.main()
