"""Interactive section-walk TUI for ``noema`` (task 0189).

Arrow keys + Enter/Esc per [[ADR — Configure CLI section walk and armed fields]].
Uses stdlib tty/termios only.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from pathlib import Path
from typing import Any, Callable

from noema.agenthost.config import AgentCliConfig, AgentConfig
from noema.agenthost.configure import commit_and_maybe_run
from noema.agenthost.configure_files import ExamplePaths
from noema.agenthost.configure_walk import SECTION_ORDER, ConfigureWalk
from noema.coordination import MODULE_REGISTRY


def _resolve_user_path(value: str, base: Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _agent_sections(
    config: AgentConfig, paths: ExamplePaths, output_dir: Path
) -> dict[str, list[dict[str, Any]]]:
    candidates = [p.name for p in paths.config_candidates] + ["(new) config.yaml"]
    if not candidates:
        candidates = ["(new) config.yaml"]
    preferred = (
        paths.preferred_config.name if paths.preferred_config is not None else "(new) config.yaml"
    )
    module_choices = sorted(MODULE_REGISTRY)
    module_value = config.noema.coordination.module or "null"
    if module_value not in module_choices:
        module_value = "null"
    return {
        "paths": [
            {"id": "config", "kind": "closed", "choices": candidates, "value": preferred},
            {"id": "programme", "kind": "open", "value": str(paths.initial_program)},
            {"id": "evaluator", "kind": "open", "value": str(paths.evaluator)},
            {"id": "output", "kind": "open", "value": str(output_dir)},
        ],
        "agent": [
            {
                "id": "mutation_cli.kind",
                "kind": "closed",
                "choices": ["claude", "codex", "opencode", "agent"],
                "value": config.mutation_cli.kind,
            },
            {
                "id": "mutation_depth",
                "kind": "closed",
                "choices": ["shallow", "deep"],
                "value": config.mutation_depth,
            },
            {
                "id": "coordination_depth",
                "kind": "closed",
                "choices": ["shallow", "deep"],
                "value": config.coordination_depth,
            },
            {
                "id": "host_log_verbosity",
                "kind": "closed",
                "choices": ["normal", "debug"],
                "value": config.host_log_verbosity,
            },
            {
                "id": "stop_children",
                "kind": "open",
                "value": "" if config.stop_children is None else str(config.stop_children),
            },
            {
                "id": "mutation_cli.model",
                "kind": "open",
                "value": config.mutation_cli.model or "",
            },
        ],
        "coordination": [
            {
                "id": "module",
                "kind": "closed",
                "choices": module_choices,
                "value": module_value,
            },
        ],
        "advanced": [
            {"id": "max_iterations", "kind": "open", "value": str(config.noema.max_iterations)},
            {
                "id": "diff_based_evolution",
                "kind": "closed",
                "choices": ["true", "false"],
                "value": "true" if config.noema.diff_based_evolution else "false",
            },
        ],
        "write_and_run": [
            {
                "id": "action",
                "kind": "closed",
                "choices": ["write", "write_and_run"],
                "value": "write_and_run",
            },
        ],
    }


def _apply_walk_to_config(
    walk: ConfigureWalk, config: AgentConfig
) -> tuple[AgentConfig, Path, Path]:
    """Push walk field values back onto config; return (config, config_path, output_dir)."""

    def _val(section: str, field_id: str) -> Any:
        for fld in walk.sections.get(section, []):
            if fld["id"] == field_id:
                return fld.get("value")
        return None

    config.mutation_cli.kind = _val("agent", "mutation_cli.kind") or config.mutation_cli.kind
    config.mutation_depth = _val("agent", "mutation_depth") or config.mutation_depth
    config.coordination_depth = _val("agent", "coordination_depth") or config.coordination_depth
    config.host_log_verbosity = _val("agent", "host_log_verbosity") or config.host_log_verbosity
    model = _val("agent", "mutation_cli.model")
    config.mutation_cli.model = model or None
    sc = _val("agent", "stop_children")
    config.stop_children = int(sc) if sc not in (None, "") else None

    mod = _val("coordination", "module")
    config.noema.coordination.module = mod if mod not in (None, "") else "null"

    mi = _val("advanced", "max_iterations")
    if mi not in (None, ""):
        config.noema.max_iterations = int(mi)
    dbe = _val("advanced", "diff_based_evolution")
    if dbe is not None:
        config.noema.diff_based_evolution = dbe == "true"

    if config.coordination_depth == "deep" and config.coordination_cli == AgentCliConfig():
        config.coordination_cli = AgentCliConfig(
            kind=config.mutation_cli.kind,
            model=config.mutation_cli.model,
        )

    cfg_name = _val("paths", "config") or "config.yaml"
    cwd = Path(_val("paths", "programme") or ".").resolve().parent
    if cfg_name == "(new) config.yaml":
        config_path = cwd / "config.yaml"
    else:
        config_path = cwd / cfg_name

    raw_out = _val("paths", "output")
    if raw_out in (None, ""):
        output_dir = cwd / "noema_agent_output"
    else:
        output_dir = _resolve_user_path(str(raw_out), cwd)
    return config, config_path, output_dir


def _os_read_char(fd: int) -> str:
    data = os.read(fd, 1)
    if not data:
        return ""
    return data.decode("latin-1")


def _finish_escape(
    first: str,
    *,
    read_char: Callable[[], str],
    ready: Callable[[], bool],
) -> str:
    """Complete an Esc sequence without mixing Python stdin buffering.

    Arrow keys arrive as ``\\x1b[A`` (CSI) or ``\\x1bOA`` (SS3). Reading the
    first byte via ``sys.stdin.read`` can leave the rest in Python's buffer
    while ``select`` on the OS fd sees nothing — arrows then look like bare Esc.
    Callers must use ``os.read`` (or an equivalent unbuffered source).
    """

    if not ready():
        return first
    second = read_char()
    if not second:
        return first
    if second == "[":  # CSI: \x1b[A, \x1b[15~, …
        seq = first + second
        while ready():
            c = read_char()
            if not c:
                break
            seq += c
            if c.isalpha() or c == "~":
                break
        return seq
    if second == "O":  # SS3 application-cursor: \x1bOA …
        if ready():
            third = read_char()
            return first + second + third if third else first + second
        return first + second
    return first + second


def _read_key(fd: int) -> str:
    ch = _os_read_char(fd)
    if ch != "\x1b":
        return ch
    return _finish_escape(
        ch,
        read_char=lambda: _os_read_char(fd),
        ready=lambda: bool(select.select([fd], [], [], 0.05)[0]),
    )


def _render(walk: ConfigureWalk) -> None:
    sys.stdout.write("\033[H\033[J")
    idx = walk.section_index + 1
    total = len(SECTION_ORDER)
    sys.stdout.write(f"── section {idx}/{total} · {walk.section_id} ──\n")
    fields = walk.fields()
    if not fields:
        sys.stdout.write("  (no fields)\n")
    for i, fld in enumerate(fields):
        mark = "▸" if i == walk.field_index else " "
        arm = " *" if walk.armed and i == walk.field_index else ""
        sys.stdout.write(f"  {mark} {fld['id']} ...... {fld.get('value')}{arm}\n")
    dirty = " dirty" if walk.dirty else ""
    editing = walk.armed and walk.fields() and walk.current_field().get("kind") == "open"
    if editing:
        hint = "type  Enter ok  Esc cancel  Backspace delete"
    elif walk.armed:
        hint = "←/→ cycle  Enter ok  Esc cancel"
    else:
        hint = "↑/↓ field  ←/→ section  Enter arm  w write&run  q quit"
    sys.stdout.write(f"\n{hint}{dirty}\n")
    sys.stdout.flush()


def _drain_stdin(fd: int) -> None:
    """Drop any pending tty bytes (e.g. leftover CSI) before cooked ``input()``."""

    while select.select([fd], [], [], 0)[0]:
        if not os.read(fd, 1024):
            break


def _cooked_prompt(fd: int, old: list, prompt: str) -> str:
    """Leave cbreak, prompt, restore cbreak. Drain so arrows don't leak into the answer."""

    sys.stdout.write(prompt)
    sys.stdout.flush()
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    _drain_stdin(fd)
    ans = input()
    tty.setcbreak(fd)
    return ans


def run_configure_tui(
    *,
    paths: ExamplePaths,
    config_path: Path,
    agent_config: AgentConfig,
    output_dir: Path,
) -> int:
    walk = ConfigureWalk(sections=_agent_sections(agent_config, paths, output_dir))
    example_cwd = paths.cwd
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        _render(walk)
        while True:
            key = _read_key(fd)
            editing_open = (
                walk.armed and bool(walk.fields()) and walk.current_field().get("kind") == "open"
            )

            if editing_open:
                if key == "\x1b":
                    walk.disarm(discard=True)
                elif key in ("\r", "\n"):
                    # Resolve path-like open fields against example cwd on accept.
                    fld = walk.current_field()
                    if fld["id"] in ("output", "programme", "evaluator") and fld.get("value"):
                        fld["value"] = str(_resolve_user_path(str(fld["value"]), example_cwd))
                    walk.disarm(discard=False)
                elif key in ("\x7f", "\b"):
                    walk.current_field()["value"] = str(walk.current_field().get("value") or "")[
                        :-1
                    ]
                elif len(key) == 1 and key.isprintable():
                    walk.current_field()["value"] = (
                        str(walk.current_field().get("value") or "") + key
                    )
                _render(walk)
                continue

            if key in ("q", "Q", "\x03"):
                if walk.dirty:
                    ans = (
                        _cooked_prompt(fd, old, "\nDiscard unsaved changes? [y/N] ").strip().lower()
                    )
                    if ans != "y":
                        _render(walk)
                        continue
                sys.stdout.write("\n")
                return 0
            # Application-cursor (SS3) → CSI so one branch handles both.
            if key in ("\x1bOA", "\x1bOB", "\x1bOC", "\x1bOD"):
                key = "\x1b[" + key[-1]
            if key in ("w", "W") or (
                walk.section_id == "write_and_run" and key in ("\r", "\n") and not walk.armed
            ):
                agent_config, config_path, output_dir = _apply_walk_to_config(walk, agent_config)
                run = True
                if walk.section_id == "write_and_run":
                    action = walk.fields()[0].get("value") if walk.fields() else "write_and_run"
                    run = action == "write_and_run"
                if run:
                    ans = _cooked_prompt(fd, old, "\nRun now? [Y/n] ").strip().lower()
                    if ans in ("n", "no"):
                        run = False
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                result = commit_and_maybe_run(
                    config_path=config_path,
                    agent_config=agent_config,
                    evaluation_file=paths.evaluator,
                    initial_program=paths.initial_program,
                    output_dir=output_dir,
                    run=run,
                )
                print(f"wrote {result['wrote']}")
                if result.get("ran"):
                    print(result.get("status"))
                return 0
            if key == "\x1b[A":
                if not walk.armed:
                    walk.move_field(-1)
            elif key == "\x1b[B":
                if not walk.armed:
                    walk.move_field(+1)
            elif key == "\x1b[C":
                if walk.armed:
                    walk.cycle_value(+1)
                else:
                    walk.move_section(+1)
            elif key == "\x1b[D":
                if walk.armed:
                    walk.cycle_value(-1)
                else:
                    walk.move_section(-1)
            elif key in ("\r", "\n"):
                if walk.armed:
                    walk.disarm(discard=False)
                else:
                    walk.arm()
            elif key == "\x1b":
                if walk.armed:
                    walk.disarm(discard=True)
            _render(walk)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
