"""Textual configure flow and live three-pane agency run monitor.

The monitor owns the interactive terminal from configuration through the end of
an agency run. Coding CLIs paint into role-owned PTYs; the host log consumes
compact records from the shared host logger.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pyte
from pyte.screens import HistoryScreen
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen, Screen
from textual.widgets import RichLog, Static

from noema.agenthost.config import AgentConfig
from noema.agenthost.configure_files import ExamplePaths, save_noema_and_agent
from noema.agenthost.configure_tui import _agent_sections, _apply_walk_to_config
from noema.agenthost.configure_walk import ConfigureWalk
from noema.agenthost.factory import create_agent_session
from noema.agenthost.session import AgentSessionAborted
from noema.budget.cli_runner import CliPtyRunner
from noema.logging import console_log_formatter, host_logger


class RoleTranscript:
    """VT-backed cumulative transcript for a single role pane."""

    def __init__(self, *, columns: int = 120, lines: int = 36, history: int = 4000) -> None:
        self._screen = HistoryScreen(columns, lines, history=history)
        self._stream = pyte.Stream(self._screen)

    def begin_session(self, label: str) -> None:
        self._stream.feed(f"\r\n── {label} ──\r\n")

    def feed(self, data: bytes) -> None:
        self._stream.feed(data.decode(errors="replace"))

    def resize(self, columns: int, lines: int) -> None:
        if columns > 0 and lines > 0:
            self._screen.resize(columns, lines)

    def lines(self) -> list[str]:
        history = [self._history_row(row) for row in self._screen.history.top]
        return history + [line.rstrip() for line in self._screen.display]

    def renderables(self) -> list[Text]:
        rows = list(self._screen.history.top) + [
            self._screen.buffer[row] for row in range(self._screen.lines)
        ]
        return [self._render_row(row) for row in rows]

    def _history_row(self, row: Any) -> str:
        return "".join(row[column].data for column in range(self._screen.columns)).rstrip()

    def _render_row(self, row: Any) -> Text:
        rendered = Text()
        for column in range(self._screen.columns):
            cell = row[column]
            rendered.append(cell.data, style=self._cell_style(cell))
        rendered.rstrip()
        return rendered

    @staticmethod
    def _cell_style(cell: Any) -> Optional[str]:
        parts = []
        if cell.fg != "default":
            parts.append(str(cell.fg).replace("bright", "bright_"))
        if cell.bg != "default":
            parts.append(f"on {str(cell.bg).replace('bright', 'bright_')}")
        if cell.bold:
            parts.append("bold")
        if cell.italics:
            parts.append("italic")
        if cell.underscore:
            parts.append("underline")
        if cell.strikethrough:
            parts.append("strike")
        if cell.reverse:
            parts.append("reverse")
        return " ".join(parts) or None


class RolePane(RichLog):
    """Focusable, observe-only rendering surface for a role-owned CLI PTY."""

    can_focus = True

    def __init__(self, title: str, *, idle_text: str, id: str) -> None:
        super().__init__(highlight=False, markup=False, wrap=True, id=id)
        self.border_title = title
        self._transcript = RoleTranscript()
        self._idle_text = idle_text

    def on_mount(self) -> None:
        self.write(self._idle_text)

    def begin_session(self, label: str) -> None:
        self._transcript.begin_session(label)
        self._render_transcript()

    def feed_bytes(self, data: bytes) -> None:
        self._transcript.feed(data)
        self._render_transcript()

    def set_idle(self, text: str) -> None:
        self._idle_text = text
        if not any(self._transcript.lines()):
            self.clear()
            self.write(text)

    def on_resize(self, _event) -> None:
        self._transcript.resize(self.size.width, self.size.height)
        self._render_transcript()

    def _render_transcript(self) -> None:
        renderables = self._transcript.renderables()
        if not any(line.plain for line in renderables):
            return
        self.clear()
        for line in renderables:
            self.write(line)
        self.scroll_end(animate=False)


class HostLogPane(RichLog):
    """Live view of compact records emitted by the shared host logger."""

    can_focus = True

    def __init__(self, *, verbosity: str) -> None:
        super().__init__(highlight=False, markup=False, wrap=True, id="host-log")
        self.border_title = "host log"
        self._verbosity = verbosity

    def append_line(self, line: str) -> None:
        self.write(line)
        self.scroll_end(animate=False)


class HostLogHandler(logging.Handler):
    """Forward shared host-log records to a Textual pane."""

    def __init__(self, append_line, *, verbosity: str) -> None:
        super().__init__(level=logging.INFO)
        self._append_line = append_line
        self.setFormatter(console_log_formatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._append_line(self.format(record))
        except Exception:
            self.handleError(record)


class ConfirmScreen(ModalScreen):
    """Small keyboard-only confirmation used for quitting and aborting."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm { width: 62; height: 5; border: round $accent; padding: 1 2; }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        yield Static(self._question, id="confirm")

    def on_key(self, event: Key) -> None:
        if event.key in ("y", "Y", "enter"):
            self.dismiss(True)
        elif event.key in ("n", "N", "escape", "q"):
            self.dismiss(False)
        event.stop()


@dataclass
class RunOutcome:
    wrote: Optional[Path] = None
    ran: bool = False
    status: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def exit_code(self) -> int:
        return 1 if self.error else 0


class ConfigureScreen(Screen):
    """Textual port of the shipped ConfigureWalk section interaction."""

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        *,
        paths: ExamplePaths,
        config_path: Path,
        agent_config: AgentConfig,
        output_dir: Path,
    ) -> None:
        super().__init__()
        self._paths = paths
        self._config_path = config_path
        self._agent_config = agent_config
        self._output_dir = output_dir
        self._walk = ConfigureWalk(sections=_agent_sections(agent_config, paths, output_dir))

    def compose(self) -> ComposeResult:
        yield Static(id="configure-title")
        yield Static(id="configure-fields")
        yield Static(id="configure-help")

    def on_mount(self) -> None:
        self._render_view()

    def action_quit(self) -> None:
        self._quit()

    def on_key(self, event: Key) -> None:
        key = event.key
        if self._editing_open_field():
            self._handle_open_edit(event)
            event.stop()
            return

        if key in ("q", "ctrl+c"):
            self._quit()
        elif key in ("w",) or (
            self._walk.section_id == "write_and_run" and key == "enter" and not self._walk.armed
        ):
            self._write_and_maybe_run()
        elif key == "up" and not self._walk.armed:
            self._walk.move_field(-1)
        elif key == "down" and not self._walk.armed:
            self._walk.move_field(+1)
        elif key == "right":
            self._walk.cycle_value(+1) if self._walk.armed else self._walk.move_section(+1)
        elif key == "left":
            self._walk.cycle_value(-1) if self._walk.armed else self._walk.move_section(-1)
        elif key == "enter":
            self._walk.disarm(discard=False) if self._walk.armed else self._walk.arm()
        elif key == "escape" and self._walk.armed:
            self._walk.disarm(discard=True)
        else:
            return
        self._render_view()
        event.stop()

    def _editing_open_field(self) -> bool:
        return (
            self._walk.armed
            and bool(self._walk.fields())
            and self._walk.current_field().get("kind") == "open"
        )

    def _handle_open_edit(self, event: Key) -> None:
        field = self._walk.current_field()
        if event.key == "escape":
            self._walk.disarm(discard=True)
        elif event.key == "enter":
            self._walk.disarm(discard=False)
        elif event.key == "backspace":
            field["value"] = str(field.get("value") or "")[:-1]
        elif event.character and event.character.isprintable():
            field["value"] = str(field.get("value") or "") + event.character
        self._render_view()

    def _quit(self) -> None:
        if self._walk.dirty:
            self.app.push_screen(
                ConfirmScreen("Discard unsaved configuration changes? [y/N]"),
                lambda confirmed: self.app.finish() if confirmed else None,
            )
        else:
            self.app.finish()

    def _write_and_maybe_run(self) -> None:
        self._agent_config, self._config_path, self._output_dir = _apply_walk_to_config(
            self._walk, self._agent_config
        )
        save_noema_and_agent(self._config_path, self._agent_config)
        self.app.outcome.wrote = self._config_path.resolve()
        action = self._walk.fields()[0].get("value") if self._walk.fields() else "write_and_run"
        if action == "write":
            self.app.finish()
            return
        self.app.push_screen(
            ConfirmScreen("Run now? [Y/n]"),
            self._after_run_confirmation,
        )

    def _after_run_confirmation(self, confirmed: bool) -> None:
        if not confirmed:
            self.app.finish()
            return
        self.app.outcome.ran = True
        self.app.push_screen(
            MonitorScreen(
                agent_config=self._agent_config,
                evaluation_file=self._paths.evaluator,
                initial_program=self._paths.initial_program,
                output_dir=self._output_dir,
            )
        )

    def _render_view(self) -> None:
        self.query_one("#configure-title", Static).update(
            f"section {self._walk.section_index + 1}/{len(self._walk.sections)} · {self._walk.section_id}"
        )
        rows = []
        for index, field in enumerate(self._walk.fields()):
            marker = "▸" if index == self._walk.field_index else " "
            armed = "  *" if self._walk.armed and index == self._walk.field_index else ""
            rows.append(f"{marker} {field['id']:<24} {field.get('value', '')}{armed}")
        self.query_one("#configure-fields", Static).update("\n".join(rows) or "(no fields)")
        if self._editing_open_field():
            hint = "type  Enter accept  Esc discard  Backspace delete"
        elif self._walk.armed:
            hint = "←/→ cycle  Enter accept  Esc discard"
        else:
            hint = "↑/↓ field  ←/→ section  Enter arm  w write  q quit"
        self.query_one("#configure-help", Static).update(
            hint + ("  · unsaved" if self._walk.dirty else "")
        )


class MonitorScreen(Screen):
    """Fixed host-log, coordination, and mutation panes for one agency run."""

    BINDINGS = [
        ("tab", "focus_next", "Next pane"),
        ("shift+tab", "focus_previous", "Previous pane"),
    ]

    def __init__(
        self,
        *,
        agent_config: AgentConfig,
        evaluation_file: Path,
        initial_program: Path,
        output_dir: Path,
        start_run: bool = True,
    ) -> None:
        super().__init__()
        self._agent_config = agent_config
        self._evaluation_file = evaluation_file
        self._initial_program = initial_program
        self._output_dir = output_dir
        self._start_run_enabled = start_run
        self._host_log = HostLogPane(verbosity=agent_config.host_log_verbosity)
        self._coordination = RolePane(
            "coordination", idle_text="coordination · idle", id="coordination-pane"
        )
        self._mutation = RolePane("mutation", idle_text="mutation · waiting", id="mutation-pane")
        self._session = None
        self._worker = None
        self._frozen = False
        self._host_log_handler: Optional[HostLogHandler] = None
        self._run_logging = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            self._host_log,
            Horizontal(self._coordination, self._mutation, id="role-panes"),
            id="monitor-layout",
        )

    def on_mount(self) -> None:
        if self._agent_config.coordination_depth == "shallow":
            self._coordination.set_idle("coordination · idle · shallow")
        if self._start_run_enabled:
            self.call_after_refresh(self._start_run)

    def on_unmount(self) -> None:
        self._detach_host_logging()

    def _append_host_log(self, line: str) -> None:
        """Append from either the run worker or the Textual event loop."""

        if self.app._thread_id == threading.get_ident():
            self._host_log.append_line(line)
        else:
            self.app.call_from_thread(self._host_log.append_line, line)

    def on_key(self, event: Key) -> None:
        if self._frozen:
            self.app.finish()
            event.stop()
            return
        if event.key in ("q", "ctrl+c"):
            self.app.push_screen(
                ConfirmScreen("Abort the live agency run? [y/N]"), self._abort_if_confirmed
            )
            event.stop()

    def _start_run(self) -> None:
        mutation_runner = CliPtyRunner(
            on_output=lambda data: self.app.call_from_thread(self._mutation.feed_bytes, data)
        )
        coordination_runner = CliPtyRunner(
            on_output=lambda data: self.app.call_from_thread(self._coordination.feed_bytes, data)
        )
        self._session = create_agent_session(
            self._agent_config,
            evaluation_file=str(self._evaluation_file.resolve()),
            initial_program_code=self._initial_program.read_text(),
            output_dir=str(self._output_dir.resolve()),
            task=self._agent_config.noema.prompt.system_message
            or "Improve the program to maximise evaluator score.",
            mutation_runner=mutation_runner,
            coordination_runner=coordination_runner,
            on_mutation_session_start=lambda label: self.app.call_from_thread(
                self._begin_mutation_session, label
            ),
            on_coordination_session_start=lambda label: self.app.call_from_thread(
                self._begin_coordination_session, label
            ),
        )
        self._run_logging = getattr(self._session, "run_logging", None)
        if self._run_logging is not None:
            self._run_logging.suspend_console()
        self._host_log_handler = HostLogHandler(
            self._append_host_log,
            verbosity=self._agent_config.host_log_verbosity,
        )
        logging.getLogger().addHandler(self._host_log_handler)
        host_logger().info("run started")
        self._worker = self.app.run_worker(self._run_host, thread=True, exit_on_error=False)

    def _begin_mutation_session(self, label: str) -> None:
        self._mutation.begin_session(label)
        host_logger().info("mutation CLI session started: %s", label)

    def _begin_coordination_session(self, label: str) -> None:
        self._coordination.begin_session(label)
        host_logger().info("coordination CLI session started: %s", label)

    def _run_host(self) -> None:
        assert self._session is not None
        try:
            status = asyncio.run(self._session.run_agent_mode())
            self.app.call_from_thread(self._freeze, status, None)
        except AgentSessionAborted:
            self.app.call_from_thread(self._freeze, {"aborted": True}, None)
        except Exception as exc:
            self.app.call_from_thread(self._freeze, None, str(exc))

    def _abort_if_confirmed(self, confirmed: bool) -> None:
        if confirmed and self._session is not None:
            host_logger().info("abort requested")
            self._session.abort()

    def _freeze(self, status: Optional[dict[str, Any]], error: Optional[str]) -> None:
        if self._frozen:
            return
        self._frozen = True
        self.app.outcome.status = status
        self.app.outcome.error = error
        if error:
            host_logger().error("run failed: %s", error)
        elif status and status.get("aborted"):
            host_logger().warning("run aborted")
        else:
            host_logger().info("run complete")
        host_logger().info("frozen · press any key to return to shell")

    def _detach_host_logging(self) -> None:
        if self._host_log_handler is not None:
            logging.getLogger().removeHandler(self._host_log_handler)
            self._host_log_handler.close()
            self._host_log_handler = None
        if self._run_logging is not None:
            self._run_logging.restore_console()


class NoemaApp(App):
    """One alternate-screen Textual session from configure to monitor exit."""

    CSS = """
    Screen { padding: 1 2; }
    #configure-title { height: 2; text-style: bold; }
    #configure-fields { height: 1fr; border: round $accent; padding: 1 2; }
    #configure-help { height: 2; color: $text-muted; }
    #monitor-layout { height: 1fr; }
    #host-log { height: 38%; border: round $accent; }
    #role-panes { height: 1fr; }
    #coordination-pane, #mutation-pane { width: 1fr; border: round $accent; }
    """

    def __init__(
        self,
        *,
        paths: ExamplePaths,
        config_path: Path,
        agent_config: AgentConfig,
        output_dir: Path,
        run_immediately: bool = False,
    ) -> None:
        super().__init__()
        self._configure = ConfigureScreen(
            paths=paths,
            config_path=config_path,
            agent_config=agent_config,
            output_dir=output_dir,
        )
        self._run_immediately = run_immediately
        self.outcome = RunOutcome()

    def on_mount(self) -> None:
        if self._run_immediately:
            save_noema_and_agent(self._configure._config_path, self._configure._agent_config)
            self.outcome.wrote = self._configure._config_path.resolve()
            self.outcome.ran = True
            self.push_screen(
                MonitorScreen(
                    agent_config=self._configure._agent_config,
                    evaluation_file=self._configure._paths.evaluator,
                    initial_program=self._configure._paths.initial_program,
                    output_dir=self._configure._output_dir,
                )
            )
            return
        self.push_screen(self._configure)

    def finish(self) -> None:
        self.exit()


def run_noema_app(
    *,
    paths: ExamplePaths,
    config_path: Path,
    agent_config: AgentConfig,
    output_dir: Path,
    run_immediately: bool = False,
) -> RunOutcome:
    """Run the alternate-screen UI and return shell-facing completion details."""

    app = NoemaApp(
        paths=paths,
        config_path=config_path,
        agent_config=agent_config,
        output_dir=output_dir,
        run_immediately=run_immediately,
    )
    app.run()
    return app.outcome
