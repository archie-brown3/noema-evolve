"""Headless coding-CLI adapters for agent-host mutation.

Supported kinds (non-interactive):
  - ``claude``   — Claude Code ``claude -p``
  - ``codex``    — Codex ``codex exec``
  - ``opencode`` — OpenCode ``opencode run`` (https://opencode.ai/docs/cli/)
  - ``agent``    — Cursor Agent ``agent -p`` (headless print mode)

The Noema mutation prompt (``system`` / ``user``) is unchanged. Each adapter
only adds a thin host envelope that names the host-owned deliverable path and
asks the CLI to edit that file in place. Prompt strategy stays on the
controller seams; this module is transport only.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence

SUPPORTED_MUTATION_CLIS = ("claude", "codex", "opencode", "agent")

_DEFAULT_BINARIES = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "agent": "agent",
}


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
    return user + "\n\n" + deliverable_envelope(
        deliverable=deliverable, parent_path=parent_path
    )


def resolve_cli_binary(kind: str, binary: Optional[str] = None) -> str:
    if kind not in SUPPORTED_MUTATION_CLIS:
        raise ValueError(
            f"unsupported mutation CLI {kind!r}; expected one of {SUPPORTED_MUTATION_CLIS}"
        )
    name = binary or _DEFAULT_BINARIES[kind]
    path = shutil.which(name) if os_path_is_bare(name) else name
    if path is None:
        raise FileNotFoundError(
            f"mutation CLI {kind!r} binary not found on PATH: {name}"
        )
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
) -> List[str]:
    """Build argv for a headless mutation CLI in ``work_dir``."""
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
        cmd = [
            exe,
            "-p",
            "--trust",
            "--force",
            "--workspace",
            work,
        ]
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


def detect_available_mutation_cli() -> Optional[str]:
    """Return the first supported CLI found on PATH, else None."""
    for kind in SUPPORTED_MUTATION_CLIS:
        try:
            resolve_cli_binary(kind)
            return kind
        except FileNotFoundError:
            continue
    return None
