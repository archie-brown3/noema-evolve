"""Headless mutation backends for the agent host.

The outer session orchestrates the evolutionary loop; each child is produced by
a MutationBackend — typically a nested coding CLI that writes a deliverable
file and exits. Deliverable paths are host-owned and deterministic under the
run ``output_dir`` (see ``mutation_layout``); they are not chosen by the Noema
prompt. Supported CLIs: Claude Code, Codex, OpenCode — see ``cli_backends``.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Union

from noema.agenthost.cli_backends import (
    SUPPORTED_MUTATION_CLIS,
    build_cli_user_message,
    build_mutation_cli_command,
)


@dataclass(frozen=True)
class MutationLayout:
    """Deterministic paths for one mutation attempt under a run output_dir.

    Mirrors the controller's on-disk layout: run diagnostics live under
    ``output_dir``, with a per-attempt subdirectory for the deliverable and
    CLI logs — not a parallel store format.
    """

    output_dir: Path
    iteration: int
    attempt: int
    file_suffix: str
    work_dir: Path
    deliverable_path: Path
    parent_path: Path
    brief_path: Path
    retry_path: Path
    stdout_log: Path
    stderr_log: Path
    system_path: Path


def mutation_layout(
    output_dir: Union[str, Path],
    iteration: int,
    attempt: int,
    *,
    file_suffix: str = ".py",
) -> MutationLayout:
    """Resolve the fixed path set for ``mutations/itNNNNNN/mNN/`` under output_dir."""
    if attempt < 1:
        raise ValueError(f"mutation attempt must be >= 1, got {attempt}")
    if not file_suffix.startswith("."):
        file_suffix = f".{file_suffix}"
    root = Path(output_dir)
    work = root / "mutations" / f"it{iteration:06d}" / f"m{attempt:02d}"
    return MutationLayout(
        output_dir=root,
        iteration=iteration,
        attempt=attempt,
        file_suffix=file_suffix,
        work_dir=work,
        deliverable_path=work / f"child{file_suffix}",
        parent_path=work / f"parent{file_suffix}",
        brief_path=work / "BRIEF.md",
        retry_path=work / "RETRY.md",
        stdout_log=work / "cli_stdout.log",
        stderr_log=work / "cli_stderr.log",
        system_path=work / "SYSTEM.md",
    )


def prepare_mutation_dir(layout: MutationLayout) -> MutationLayout:
    """Create the attempt directory; paths themselves stay unchanged."""
    layout.work_dir.mkdir(parents=True, exist_ok=True)
    return layout


def write_deliverable(path: Union[str, Path], code: str) -> Path:
    """Host-owned write of child code to the layout's deliverable path."""
    deliverable = Path(path)
    deliverable.parent.mkdir(parents=True, exist_ok=True)
    deliverable.write_text(code)
    return deliverable


def read_deliverable(path: Union[str, Path]) -> Optional[str]:
    """Read child code from the layout deliverable, or None if missing/empty."""
    deliverable = Path(path)
    if not deliverable.is_file():
        return None
    code = deliverable.read_text()
    return code if code.strip() else None


@dataclass
class MutationRequest:
    """One headless mutation call.

    ``prompt`` is the controller-shaped ``{system, user}`` dict from
    ``build_mutation_prompt`` + ``inject_advice`` (or the full_executor_prompt
    path). The deliverable path is host-owned via ``mutation_layout``.
    """

    prompt: Dict[str, str]
    parent_code: str
    work_dir: Path
    deliverable_path: Path
    timeout_s: float
    retry_brief: Optional[str] = None
    layout: Optional[MutationLayout] = None

    @property
    def brief(self) -> str:
        """User half of the mutation prompt (CLI / log convenience)."""
        return self.prompt.get("user", "")


@dataclass
class MutationResult:
    ok: bool
    code: Optional[str] = None
    error: Optional[str] = None
    backend_trace: Dict = field(default_factory=dict)


class MutationBackend(Protocol):
    def run(self, request: MutationRequest) -> MutationResult: ...


class FakeMutationBackend:
    """Test double: return canned / callable-produced code, or a hard failure."""

    def __init__(
        self,
        *,
        code: Optional[str] = None,
        producer: Optional[Callable[[MutationRequest], str]] = None,
        fail_error: Optional[str] = None,
    ):
        if fail_error is None and code is None and producer is None:
            raise ValueError("FakeMutationBackend needs code, producer, or fail_error")
        self._code = code
        self._producer = producer
        self._fail_error = fail_error

    def run(self, request: MutationRequest) -> MutationResult:
        request.work_dir.mkdir(parents=True, exist_ok=True)
        if self._fail_error is not None:
            return MutationResult(
                ok=False,
                error=self._fail_error,
                backend_trace={
                    "backend": "fake",
                    "work_dir": str(request.work_dir),
                    "deliverable": str(request.deliverable_path),
                },
            )
        code = self._producer(request) if self._producer is not None else self._code
        assert code is not None
        write_deliverable(request.deliverable_path, code)
        return MutationResult(
            ok=True,
            code=code,
            backend_trace={
                "backend": "fake",
                "work_dir": str(request.work_dir),
                "deliverable": str(request.deliverable_path),
                "exit_code": 0,
            },
        )


class CliMutationBackend:
    """Spawn a headless coding CLI; child code is the deliverable file + exit 0.

    Construct with either:
      - ``kind`` in {claude, codex, opencode, agent} — argv built by ``cli_backends``
      - ``command`` — fixed argv (tests / custom wrappers)

    Before spawn the host writes SYSTEM.md / BRIEF.md, seeds the deliverable
    with the parent program, and (for kind-based runs) appends a deliverable
    envelope to the CLI user message. Env always includes MUTATION_* paths.
    """

    def __init__(
        self,
        kind: Optional[str] = None,
        command: Optional[List[str]] = None,
        *,
        binary: Optional[str] = None,
        model: Optional[str] = None,
        extra_args: Optional[Sequence[str]] = None,
    ):
        if command is None and kind is None:
            raise ValueError("CliMutationBackend requires kind= or command=")
        if command is not None and not command:
            raise ValueError("CliMutationBackend command must be non-empty")
        if kind is not None and kind not in SUPPORTED_MUTATION_CLIS and command is None:
            raise ValueError(
                f"unsupported mutation CLI {kind!r}; expected one of "
                f"{SUPPORTED_MUTATION_CLIS}"
            )
        self.kind = kind or "custom"
        self.command = list(command) if command is not None else None
        self.binary = binary
        self.model = model
        self.extra_args = list(extra_args or ())

    def run(self, request: MutationRequest) -> MutationResult:
        layout = request.layout
        if layout is not None:
            prepare_mutation_dir(layout)
            work = layout.work_dir
            brief_path = layout.brief_path
            parent_path = layout.parent_path
            retry_path = layout.retry_path
            stdout_path = layout.stdout_log
            stderr_path = layout.stderr_log
            deliverable = layout.deliverable_path
            system_path = layout.system_path
        else:
            work = request.work_dir
            work.mkdir(parents=True, exist_ok=True)
            brief_path = work / "BRIEF.md"
            parent_path = work / f"parent{request.deliverable_path.suffix or '.py'}"
            retry_path = work / "RETRY.md"
            stdout_path = work / "cli_stdout.log"
            stderr_path = work / "cli_stderr.log"
            deliverable = request.deliverable_path
            system_path = work / "SYSTEM.md"

        system_text = request.prompt.get("system", "")
        user_text = request.prompt.get("user", "")
        system_path.write_text(system_text)
        brief_path.write_text(user_text)
        parent_path.write_text(request.parent_code)
        # Seed deliverable so the coding CLI edits in place.
        write_deliverable(deliverable, request.parent_code)
        if request.retry_brief:
            retry_path.write_text(request.retry_brief)

        env = os.environ.copy()
        env["MUTATION_BRIEF_PATH"] = str(brief_path)
        env["MUTATION_SYSTEM_PATH"] = str(system_path)
        env["MUTATION_PARENT_PATH"] = str(parent_path)
        env["MUTATION_DELIVERABLE"] = str(deliverable)
        if request.retry_brief:
            env["MUTATION_RETRY_BRIEF_PATH"] = str(retry_path)

        if self.command is not None:
            argv = list(self.command)
            cli_user = user_text
        else:
            cli_user = build_cli_user_message(
                request.prompt,
                deliverable=deliverable,
                parent_path=parent_path,
            )
            argv = build_mutation_cli_command(
                self.kind,
                work_dir=work,
                system_path=system_path,
                user_message=cli_user,
                binary=self.binary,
                model=self.model,
                extra_args=self.extra_args,
            )

        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=str(work),
                env=env,
                capture_output=True,
                text=True,
                timeout=request.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            wall = time.monotonic() - started
            stdout_path.write_text(_as_text(exc.stdout))
            stderr_path.write_text(_as_text(exc.stderr))
            return MutationResult(
                ok=False,
                error=f"mutation CLI timed out after {request.timeout_s}s",
                backend_trace={
                    "backend": self.kind,
                    "work_dir": str(work),
                    "deliverable": str(deliverable),
                    "argv": argv,
                    "exit_code": None,
                    "wall_s": wall,
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                },
            )

        stdout_path.write_text(completed.stdout or "")
        stderr_path.write_text(completed.stderr or "")
        wall = time.monotonic() - started
        trace = {
            "backend": self.kind,
            "work_dir": str(work),
            "exit_code": completed.returncode,
            "wall_s": wall,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "deliverable": str(deliverable),
            "argv": argv,
        }

        if completed.returncode != 0:
            return MutationResult(
                ok=False,
                error=f"mutation CLI exited {completed.returncode}",
                backend_trace=trace,
            )

        code = read_deliverable(deliverable)
        if code is None:
            missing = "empty" if deliverable.is_file() else "missing"
            return MutationResult(
                ok=False,
                error=f"deliverable {missing}: {deliverable}",
                backend_trace=trace,
            )

        # Unchanged seed means the CLI never wrote a child.
        if code.strip() == request.parent_code.strip():
            return MutationResult(
                ok=False,
                error="deliverable unchanged from parent (CLI wrote no mutation)",
                backend_trace=trace,
            )

        return MutationResult(ok=True, code=code, backend_trace=trace)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
