"""Focused seams for the Textual agency run monitor."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.app import App

from noema.agenthost.config import AgentConfig
from noema.agenthost.configure_files import ExamplePaths
from noema.agenthost.monitor import (
    MonitorScreen,
    NoemaApp,
    RoleTranscript,
    RunOutcome,
    format_attempt_trace,
    format_host_log_record,
)
from noema.agenthost.session import AgentSessionAborted
from noema.trace import AttemptTraceWriter


class TestAttemptTraceFormatting(unittest.TestCase):
    def test_score_is_compact_and_stable(self):
        line = format_attempt_trace(
            {
                "iteration": 2,
                "attempt": 3,
                "outcome": "accepted",
                "evaluation": {"combined_score": 1.23456},
            }
        )
        self.assertEqual(line, "it000002 m03  accepted  score=1.235")

    def test_error_is_used_when_no_score_exists(self):
        line = format_attempt_trace(
            {
                "iteration": 0,
                "attempt": 1,
                "outcome": "evaluation_failure",
                "error": "evaluator failed\non second line",
            }
        )
        self.assertEqual(
            line,
            "it000000 m01  evaluation_failure  evaluator failed on second line",
        )

    def test_accepted_verbosity_omits_non_accepted_attempts(self):
        line = format_host_log_record(
            {"iteration": 0, "attempt": 1, "outcome": "evaluation_failure"},
            verbosity="accepted",
        )
        self.assertIsNone(line)

    def test_full_verbosity_uses_the_jsonl_record_shape(self):
        record = {
            "iteration": 0,
            "attempt": 1,
            "outcome": "evaluation_failure",
            "error": "evaluator failed",
        }
        line = format_host_log_record(record, verbosity="full")
        self.assertEqual(json.loads(line), record)


class TestRoleTranscript(unittest.TestCase):
    def test_pty_paint_and_prior_session_segments_remain_in_scrollback(self):
        transcript = RoleTranscript(columns=48, lines=8)
        transcript.begin_session("it000000/m01")
        transcript.feed(b"\x1b[32mfirst CLI paint\x1b[0m\r\n")
        transcript.begin_session("it000001/m01")
        transcript.feed(b"second CLI paint\r\n")

        rendered = "\n".join(transcript.lines())
        self.assertIn("── it000000/m01 ──", rendered)
        self.assertIn("first CLI paint", rendered)
        self.assertIn("── it000001/m01 ──", rendered)
        self.assertIn("second CLI paint", rendered)


class TestTextualScreens(unittest.TestCase):
    def test_configure_screen_preserves_section_walk_navigation(self):
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                program = root / "initial_program.py"
                evaluator = root / "evaluator.py"
                program.write_text("def f():\n    return 1\n")
                evaluator.write_text("def evaluate(_):\n    return {'combined_score': 1}\n")
                paths = ExamplePaths(
                    cwd=root,
                    initial_program=program,
                    evaluator=evaluator,
                    preferred_config=None,
                    config_candidates=(),
                )
                app = NoemaApp(
                    paths=paths,
                    config_path=root / "config.yaml",
                    agent_config=AgentConfig(),
                    output_dir=root / "out",
                )
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    title = app.screen.query_one("#configure-title").render()
                    self.assertIn("paths", str(title))
                    await pilot.press("right")
                    title = app.screen.query_one("#configure-title").render()
                    self.assertIn("agent", str(title))

        asyncio.run(scenario())

    def test_monitor_keeps_all_three_panes_when_coordination_is_shallow(self):
        class Harness(App):
            def on_mount(self) -> None:
                self.push_screen(
                    MonitorScreen(
                        agent_config=AgentConfig(),
                        evaluation_file=Path("evaluator.py"),
                        initial_program=Path("initial_program.py"),
                        output_dir=Path("output"),
                        start_run=False,
                    )
                )

        async def scenario() -> None:
            app = Harness()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.screen.query_one("#host-log"))
                self.assertIsNotNone(app.screen.query_one("#coordination-pane"))
                self.assertIsNotNone(app.screen.query_one("#mutation-pane"))

        asyncio.run(scenario())

    def test_monitor_starts_the_host_and_ctrl_c_aborts_after_confirmation(self):
        class FakeSession:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.abort_called = threading.Event()
                self.trace_written = threading.Event()
                self._attempt_trace_callback = None
                self._output_dir = None

            async def run_agent_mode(self):
                self.started.set()
                assert self._attempt_trace_callback is not None
                assert self._output_dir is not None
                self._output_dir.mkdir(parents=True, exist_ok=True)
                trace_path = self._output_dir / "attempt_trace.jsonl"
                AttemptTraceWriter(
                    str(trace_path),
                    on_write=self._attempt_trace_callback,
                ).write(
                    iteration=0,
                    attempt=0,
                    outcome="accepted",
                    evaluation={"metrics": {"combined_score": 1.25}},
                )
                self.trace_written.set()
                while not self.abort_called.is_set():
                    await asyncio.sleep(0.01)
                raise AgentSessionAborted("agency run aborted by operator")

            def abort(self) -> None:
                self.abort_called.set()

        class Harness(App):
            def __init__(
                self,
                session: FakeSession,
                evaluator: Path,
                program: Path,
                output_dir: Path,
            ) -> None:
                super().__init__()
                self.session = session
                self.evaluator = evaluator
                self.program = program
                self.output_dir = output_dir
                self.outcome = RunOutcome()

            def on_mount(self) -> None:
                self.push_screen(
                    MonitorScreen(
                        agent_config=AgentConfig(),
                        evaluation_file=self.evaluator,
                        initial_program=self.program,
                        output_dir=self.output_dir,
                    )
                )

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                program = root / "initial_program.py"
                evaluator = root / "evaluator.py"
                program.write_text("def f():\n    return 1\n")
                evaluator.write_text("def evaluate(_):\n    return {'combined_score': 1}\n")
                session = FakeSession()
                app = Harness(session, evaluator, program, root / "output")

                def create_fake_session(*_args, **kwargs):
                    session._attempt_trace_callback = kwargs["attempt_trace_callback"]
                    session._output_dir = Path(kwargs["output_dir"])
                    return session

                with patch(
                    "noema.agenthost.monitor.create_agent_session",
                    side_effect=create_fake_session,
                ):
                    async with app.run_test(size=(100, 30)) as pilot:
                        await pilot.pause()
                        self.assertTrue(session.started.wait(timeout=0.1))
                        self.assertTrue(session.trace_written.wait(timeout=0.1))
                        await pilot.pause()
                        host_log = app.screen.query_one("#host-log")
                        self.assertIn(
                            "run started", "\n".join(line.text for line in host_log.lines)
                        )
                        self.assertIn(
                            "it000000 m00  accepted  score=1.25",
                            "\n".join(line.text for line in host_log.lines),
                        )

                        await pilot.press("ctrl+c")
                        await pilot.press("enter")
                        await pilot.pause()

                        self.assertTrue(session.abort_called.is_set())
                        self.assertTrue(app.screen._frozen)
                        self.assertIn(
                            "run aborted", "\n".join(line.text for line in host_log.lines)
                        )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
