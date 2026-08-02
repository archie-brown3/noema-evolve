"""Headless coding-CLI transport for mutation subprocesses.

Supported kinds (non-interactive):
  - ``claude``   — Claude Code ``claude -p``
  - ``codex``    — Codex ``codex exec``
  - ``opencode`` — OpenCode ``opencode run`` (https://opencode.ai/docs/cli/)
  - ``agent``    — Cursor Agent ``agent -p`` (headless print mode)

Argv builders and subprocess spawn live here; mutation semantics (layout,
deliverable read/write) stay in ``noema.agenthost.mutation``.
"""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

SUPPORTED_MUTATION_CLIS = ("claude", "codex", "opencode", "agent")

_DEFAULT_BINARIES = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "agent": "agent",
}


@dataclass(frozen=True)
class CliRunResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    wall_s: float
    timed_out: bool


class CliRunner:
    """Transport-only subprocess primitive for headless mutation CLIs."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CliRunResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            wall = time.monotonic() - started
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr)
            stdout_path.write_text(stdout)
            stderr_path.write_text(stderr)
            return CliRunResult(
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                wall_s=wall,
                timed_out=True,
            )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        stdout_path.write_text(stdout)
        stderr_path.write_text(stderr)
        wall = time.monotonic() - started
        return CliRunResult(
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            wall_s=wall,
            timed_out=False,
        )


PtyOutputCallback = Callable[[bytes], None]


class CliPtyRunner:
    """Run a coding CLI on one controlling PTY and optionally mirror its paint.

    The PTY is deliberately the common spawn primitive for both run-monitor and
    headless agency launches. A real terminal merges stdout/stderr in the order
    the coding CLI painted them; ``stdout_path`` therefore receives that merged
    transcript and ``stderr_path`` remains an empty compatibility file.
    """

    def __init__(self, *, on_output: Optional[PtyOutputCallback] = None) -> None:
        self._on_output = on_output
        self._lock = threading.Lock()
        self._active_pid: Optional[int] = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active_pid is not None

    def abort(self) -> None:
        """Best-effort stop for the currently active CLI session, if any."""

        with self._lock:
            pid = self._active_pid
        if pid is not None:
            self._terminate_process_group(pid, signal.SIGTERM)

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CliRunResult:
        """Execute ``argv`` on a controlling PTY and return the merged paint."""

        if not argv:
            raise ValueError("argv must not be empty")
        started = time.monotonic()
        master_fd, slave_fd = pty.openpty()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child immediately execs.
            try:
                os.setsid()
                os.close(master_fd)
                # Opening the slave after setsid makes it the controlling terminal.
                slave_name = os.ttyname(slave_fd)
                controlling_fd = os.open(slave_name, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(controlling_fd, fd)
                if controlling_fd > 2:
                    os.close(controlling_fd)
                if slave_fd > 2:
                    os.close(slave_fd)
                os.chdir(str(cwd))
                os.execvpe(argv[0], argv, env)
            except BaseException as exc:
                os.write(2, f"noema CLI PTY exec failed: {exc}\n".encode())
                os._exit(127)

        os.close(slave_fd)
        with self._lock:
            self._active_pid = pid

        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        output = bytearray()
        timed_out = False
        child_status: Optional[int] = None
        eof = False
        deadline = started + timeout_s
        termination_started: Optional[float] = None
        try:
            stderr_path.write_text("")
            with stdout_path.open("wb") as stdout_log:
                while child_status is None or not eof:
                    if child_status is None:
                        waited_pid, status = os.waitpid(pid, os.WNOHANG)
                        if waited_pid == pid:
                            child_status = status

                    now = time.monotonic()
                    if child_status is None and termination_started is None and now >= deadline:
                        timed_out = True
                        self._terminate_process_group(pid, signal.SIGTERM)
                        termination_started = now
                        deadline = now + 0.25
                    elif (
                        child_status is None and termination_started is not None and now >= deadline
                    ):
                        self._terminate_process_group(pid, signal.SIGKILL)
                        deadline = now + 0.25

                    if not eof:
                        wait_s = 0.05
                        if child_status is None:
                            wait_s = max(0.0, min(wait_s, deadline - time.monotonic()))
                        readable, _, _ = select.select([master_fd], [], [], wait_s)
                        if readable:
                            try:
                                chunk = os.read(master_fd, 65536)
                            except OSError as exc:
                                if exc.errno not in (errno.EIO, errno.EBADF):
                                    raise
                                chunk = b""
                            if chunk:
                                output.extend(chunk)
                                stdout_log.write(chunk)
                                stdout_log.flush()
                                if self._on_output is not None:
                                    try:
                                        self._on_output(chunk)
                                    except Exception:
                                        # UI repaint failures must not alter the study run.
                                        pass
                            else:
                                eof = True
                    else:
                        time.sleep(0.01)
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            with self._lock:
                if self._active_pid == pid:
                    self._active_pid = None

        merged = output.decode(errors="replace")
        return CliRunResult(
            exit_code=None if timed_out else _wait_status_exit_code(child_status),
            stdout=merged,
            stderr="",
            wall_s=time.monotonic() - started,
            timed_out=timed_out,
        )

    @staticmethod
    def _terminate_process_group(pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass


def _wait_status_exit_code(status: Optional[int]) -> Optional[int]:
    if status is None:
        return None
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return None


def deliverable_envelope(*, deliverable: Path, parent_path: Path) -> str:
    """Host-owned write contract appended to the CLI user message (not the Noema brief)."""
    return (
        "# Host mutation contract\n"
        "The host only admits the program written to this exact file path:\n"
        f"  {deliverable}\n"
        "That file is already seeded with the parent program (also saved at "
        f"{parent_path}). Edit the deliverable in place so it contains the "
        "complete improved program, save it, then stop. Do not put the program "
        "only in chat output — stdout is logs, not the payload.\n"
    )


def build_cli_user_message(
    prompt: Dict[str, str],
    *,
    deliverable: Path,
    parent_path: Path,
) -> str:
    user = prompt.get("user", "")
    return user + "\n\n" + deliverable_envelope(deliverable=deliverable, parent_path=parent_path)


def resolve_cli_binary(kind: str, binary: Optional[str] = None) -> str:
    if kind not in SUPPORTED_MUTATION_CLIS:
        raise ValueError(
            f"unsupported mutation CLI {kind!r}; expected one of {SUPPORTED_MUTATION_CLIS}"
        )
    name = binary or _DEFAULT_BINARIES[kind]
    path = shutil.which(name) if os_path_is_bare(name) else name
    if path is None:
        raise FileNotFoundError(f"mutation CLI {kind!r} binary not found on PATH: {name}")
    return path


def os_path_is_bare(name: str) -> bool:
    return "/" not in name and not name.startswith(".")


def build_mutation_cli_command(
    kind: str,
    *,
    work_dir: Path,
    system_path: Path,
    user_message: str,
    binary: Optional[str] = None,
    model: Optional[str] = None,
    extra_args: Optional[Sequence[str]] = None,
    mcp_config_path: Optional[Path] = None,
) -> List[str]:
    """Build argv for a headless mutation CLI in ``work_dir``.

    ``mcp_config_path`` attaches the inner-session MCP server (0179) using the
    supported configuration mechanism for the requested CLI.
    """
    exe = resolve_cli_binary(kind, binary)
    extra = list(extra_args or ())
    work = str(work_dir)
    system_file = str(system_path)

    if kind == "claude":
        cmd = [
            exe,
            "-p",
            "--bare",
            "--dangerously-skip-permissions",
            "--system-prompt-file",
            system_file,
        ]
        if model:
            cmd.extend(["--model", model])
        if mcp_config_path is not None:
            cmd.extend(["--mcp-config", str(mcp_config_path), "--strict-mcp-config"])
        cmd.extend(extra)
        cmd.append(user_message)
        return cmd

    if kind == "codex":
        # workspace-write + skip approvals: mutation work_dir is the sandbox.
        cmd = [
            exe,
            "exec",
            "-C",
            work,
            "--skip-git-repo-check",
            "-s",
            "workspace-write",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if mcp_config_path is not None:
            cmd.extend(_codex_mcp_config_args(mcp_config_path))
        if model:
            cmd.extend(["-m", model])
        cmd.extend(extra)
        # Codex has no separate system-prompt file flag; fold system into the message.
        system_text = system_path.read_text() if system_path.is_file() else ""
        message = (
            f"# System\n{system_text}\n\n# User\n{user_message}"
            if system_text.strip()
            else user_message
        )
        cmd.append(message)
        return cmd

    if kind == "opencode":
        if mcp_config_path is not None:
            _write_opencode_project_config(work_dir, mcp_config_path)
        # `--file` is an array flag and will swallow following positionals unless
        # the message is protected by `--` (or placed before `--file`).
        cmd = [
            exe,
            "run",
            "--dir",
            work,
            "--auto",
            "--file",
            system_file,
        ]
        if model:
            cmd.extend(["-m", model])
        cmd.extend(extra)
        cmd.extend(["--", user_message])
        return cmd

    if kind == "agent":
        if mcp_config_path is not None:
            _write_cursor_project_mcp_config(work_dir, mcp_config_path)
        cmd = [
            exe,
            "-p",
            "--trust",
            "--force",
            "--workspace",
            work,
        ]
        if mcp_config_path is not None:
            cmd.append("--approve-mcps")
        if model:
            cmd.extend(["--model", model])
        cmd.extend(extra)
        system_text = system_path.read_text() if system_path.is_file() else ""
        message = (
            f"# System\n{system_text}\n\n# User\n{user_message}"
            if system_text.strip()
            else user_message
        )
        cmd.append(message)
        return cmd

    raise ValueError(f"unsupported mutation CLI {kind!r}")


def _load_mcp_servers(config_path: Path) -> Dict[str, Dict[str, Any]]:
    config = json.loads(config_path.read_text())
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"MCP config has no mcpServers: {config_path}")
    return servers


def _codex_mcp_config_args(config_path: Path) -> List[str]:
    args: List[str] = []
    for name, server in _load_mcp_servers(config_path).items():
        command = server.get("command")
        server_args = server.get("args", [])
        if not isinstance(command, str) or not command:
            raise ValueError(f"MCP server {name!r} has no stdio command")
        if not isinstance(server_args, list) or not all(
            isinstance(item, str) for item in server_args
        ):
            raise ValueError(f"MCP server {name!r} args must be a list of strings")
        key = f"mcp_servers.{name}"
        args.extend(["-c", f"{key}.command={json.dumps(command)}"])
        args.extend(["-c", f"{key}.args={json.dumps(server_args)}"])
        args.extend(["-c", f"{key}.enabled=true"])
        args.extend(["-c", f"{key}.required=true"])
        args.extend(["-c", f'{key}.default_tools_approval_mode="approve"'])
    return args


def _write_opencode_project_config(work_dir: Path, config_path: Path) -> Path:
    servers = {}
    for name, server in _load_mcp_servers(config_path).items():
        command = server.get("command")
        server_args = server.get("args", [])
        if not isinstance(command, str) or not command:
            raise ValueError(f"MCP server {name!r} has no stdio command")
        if not isinstance(server_args, list) or not all(
            isinstance(item, str) for item in server_args
        ):
            raise ValueError(f"MCP server {name!r} args must be a list of strings")
        entry: Dict[str, Any] = {
            "type": "local",
            "command": [command, *server_args],
            "enabled": True,
        }
        if isinstance(server.get("env"), dict):
            entry["environment"] = server["env"]
        servers[name] = entry
    path = work_dir / "opencode.json"
    path.write_text(
        json.dumps(
            {"$schema": "https://opencode.ai/config.json", "mcp": servers},
            indent=2,
        )
    )
    return path


def _write_cursor_project_mcp_config(work_dir: Path, config_path: Path) -> Path:
    cursor_dir = work_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    path = cursor_dir / "mcp.json"
    path.write_text(config_path.read_text())
    return path


def detect_available_mutation_cli() -> Optional[str]:
    """Return the first supported CLI found on PATH, else None."""
    for kind in SUPPORTED_MUTATION_CLIS:
        try:
            resolve_cli_binary(kind)
            return kind
        except FileNotFoundError:
            continue
    return None


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
