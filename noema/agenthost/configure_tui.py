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
from copy import deepcopy
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Callable, Optional, Union, get_args, get_origin, get_type_hints

import yaml

from noema.agenthost.config import AgentCliConfig, AgentConfig
from noema.agenthost.configure import commit_and_maybe_run
from noema.agenthost.configure_files import ExamplePaths
from noema.agenthost.configure_schema import (
    advanced_field_groups,
    coordination_param_specs,
)
from noema.agenthost.configure_schema import display_config_value as _display_value
from noema.agenthost.configure_schema import make_field as _make_field
from noema.agenthost.configure_walk import SECTION_ORDER, ConfigureWalk
from noema.coordination import MODULE_REGISTRY


def render_config_overview(
    config: AgentConfig,
    *,
    config_path: Path,
    output_dir: Path,
    action: str,
) -> str:
    """Render a read-only summary of the effective configuration."""

    noema = config.noema
    lines = [
        f"config: {Path(config_path)}",
        f"output: {Path(output_dir)}",
        (
            "mutation_cli: "
            f"kind={config.mutation_cli.kind}, model={config.mutation_cli.model or '-'}, "
            f"depth={config.mutation_depth}, timeout_s={config.mutation_cli.timeout_s:g}"
        ),
        (
            "coordination_cli: "
            f"model={config.coordination_cli.model or '-'}, depth={config.coordination_depth}"
        ),
        f"coordination.module: {noema.coordination.module}",
    ]
    for name, value in noema.coordination.params.items():
        lines.append(f"  {name}: {_display_value(value)}")
    lines.extend(
        [
            f"substrate.kind: {noema.substrate.kind}",
            f"  steps_per_generation: {_display_value(noema.substrate.steps_per_generation)}",
        ]
    )
    if noema.substrate.kind == "cvt":
        lines.extend(
            [
                f"  cvt_n_centroids: {noema.substrate.cvt_n_centroids}",
                (
                    "  cvt_behavior_features: "
                    f"{_display_value(noema.substrate.cvt_behavior_features)}"
                ),
                f"  cvt_num_regions: {_display_value(noema.substrate.cvt_num_regions)}",
            ]
        )
    lines.extend(
        [
            f"selection.policy: {noema.selection.policy}",
            f"  seed: {_display_value(noema.selection.seed)}",
        ]
    )
    if noema.selection.policy == "boltzmann":
        lines.extend(
            [
                ("  boltzmann_temperature: " f"{noema.selection.boltzmann_temperature:g}"),
                (
                    "  boltzmann_exploration_rate: "
                    f"{noema.selection.boltzmann_exploration_rate:g}"
                ),
            ]
        )
    elif noema.selection.policy == "uct":
        lines.extend(
            [
                f"  initial_exploration: {noema.selection.initial_exploration:g}",
                f"  widening_alpha: {noema.selection.widening_alpha:g}",
            ]
        )
    lines.extend(
        [
            f"max_iterations: {noema.max_iterations}",
            f"checkpoint_interval: {noema.checkpoint_interval}",
            f"random_seed: {noema.random_seed}",
            f"diff_based_evolution: {_display_value(noema.diff_based_evolution)}",
            f"retry_enabled: {_display_value(noema.retry_enabled)}",
            f"retry_cap: {noema.retry_cap}",
            f"retry_on: {noema.retry_on}",
            f"num_inspirations: {noema.num_inspirations}",
            f"num_top_programs: {noema.num_top_programs}",
            f"num_previous_programs: {noema.num_previous_programs}",
            f"action: {action}",
        ]
    )
    return "\n".join(lines)


def _resolve_user_path(value: str, base: Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _agent_sections(
    config: AgentConfig,
    paths: ExamplePaths,
    output_dir: Path,
    draft_values: Optional[dict[str, Any]] = None,
) -> dict[str, list[dict[str, Any]]]:
    drafts = draft_values or {}

    def field(field_id: str, value: Any, **kwargs: Any) -> dict[str, Any]:
        result = _make_field(field_id, value, **kwargs)
        if field_id in drafts:
            result["value"] = drafts[field_id]
        return result

    candidates = [p.name for p in paths.config_candidates] + ["(new) config.yaml"]
    if not candidates:
        candidates = ["(new) config.yaml"]
    preferred = (
        paths.preferred_config.name if paths.preferred_config is not None else "(new) config.yaml"
    )
    module_choices = sorted(MODULE_REGISTRY)
    module_value = drafts.get("coordination.module", config.noema.coordination.module or "null")
    if module_value not in module_choices:
        module_value = "null"
    coordination_fields = [
        field(
            "coordination.module",
            module_value,
            choices=tuple(module_choices),
        ),
        field("coordination.seed", config.noema.coordination.seed, value_type="optional_int"),
    ]
    for spec in coordination_param_specs(module_value, main=True):
        coordination_fields.append(
            field(
                spec.path,
                config.noema.coordination.params.get(spec.name, spec.default),
                value_type=spec.value_type,
                choices=spec.choices if spec.writable else (),
                kind="open" if spec.writable else "readonly",
            )
        )

    def path_field(field_id: str, **spec: Any) -> dict[str, Any]:
        # Path fields bypass _make_field, so drafts have to be re-applied here too.
        result: dict[str, Any] = {"id": field_id, **spec}
        if field_id in drafts:
            result["value"] = drafts[field_id]
        return result

    return {
        "paths": [
            path_field("config", kind="closed", choices=candidates, value=preferred),
            path_field("programme", kind="open", value=str(paths.initial_program)),
            path_field("evaluator", kind="open", value=str(paths.evaluator)),
            path_field("output", kind="open", value=str(output_dir)),
        ],
        "agent": [
            field(
                "mutation_cli.kind",
                config.mutation_cli.kind,
                choices=("claude", "codex", "opencode", "agent"),
            ),
            field(
                "mutation_depth",
                config.mutation_depth,
                choices=("shallow", "deep"),
            ),
            field("mutation_cli.model", config.mutation_cli.model, value_type="optional_str"),
            field("mutation_cli.timeout_s", config.mutation_cli.timeout_s, value_type="float"),
            field(
                "coordination_depth",
                config.coordination_depth,
                choices=("shallow", "deep"),
            ),
            field(
                "coordination_cli.model",
                config.coordination_cli.model,
                value_type="optional_str",
            ),
            field("stop_children", config.stop_children, value_type="optional_int"),
        ],
        "coordination": coordination_fields,
        "substrate": [
            field(
                "substrate.kind",
                config.noema.substrate.kind,
                choices=("islands", "tree", "cvt", "flat"),
            ),
            field(
                "substrate.steps_per_generation",
                config.noema.substrate.steps_per_generation,
                value_type="optional_int",
            ),
            *(
                [
                    field(
                        "substrate.cvt_n_centroids",
                        config.noema.substrate.cvt_n_centroids,
                        value_type="int",
                    ),
                    field(
                        "substrate.cvt_behavior_features",
                        config.noema.substrate.cvt_behavior_features,
                        value_type="optional_list",
                    ),
                    field(
                        "substrate.cvt_num_regions",
                        config.noema.substrate.cvt_num_regions,
                        value_type="optional_int",
                    ),
                ]
                if drafts.get("substrate.kind", config.noema.substrate.kind) == "cvt"
                else []
            ),
        ],
        "selection": [
            field(
                "selection.policy",
                config.noema.selection.policy,
                choices=(
                    "substrate_default",
                    "stock_openevolve",
                    "boltzmann",
                    "uct",
                    "cvt_ucb",
                    "hifo_prob_rank",
                ),
            ),
            field("selection.seed", config.noema.selection.seed, value_type="optional_int"),
            *(
                [
                    field(
                        "selection.boltzmann_temperature",
                        config.noema.selection.boltzmann_temperature,
                        value_type="float",
                    ),
                    field(
                        "selection.boltzmann_exploration_rate",
                        config.noema.selection.boltzmann_exploration_rate,
                        value_type="float",
                    ),
                ]
                if drafts.get("selection.policy", config.noema.selection.policy) == "boltzmann"
                else []
            ),
            *(
                [
                    field(
                        "selection.initial_exploration",
                        config.noema.selection.initial_exploration,
                        value_type="float",
                    ),
                    field(
                        "selection.widening_alpha",
                        config.noema.selection.widening_alpha,
                        value_type="float",
                    ),
                ]
                if drafts.get("selection.policy", config.noema.selection.policy) == "uct"
                else []
            ),
        ],
        "evolution": [
            field("max_iterations", config.noema.max_iterations, value_type="int"),
            field("checkpoint_interval", config.noema.checkpoint_interval, value_type="int"),
            field("random_seed", config.noema.random_seed, value_type="int"),
            field(
                "diff_based_evolution",
                config.noema.diff_based_evolution,
                value_type="bool",
                choices=("true", "false"),
            ),
            field(
                "retry_enabled",
                config.noema.retry_enabled,
                value_type="bool",
                choices=("true", "false"),
            ),
            field("retry_cap", config.noema.retry_cap, value_type="int"),
            field(
                "retry_on",
                config.noema.retry_on,
                choices=("failure", "non_improvement"),
            ),
            field("num_inspirations", config.noema.num_inspirations, value_type="int"),
            field("num_top_programs", config.noema.num_top_programs, value_type="int"),
            field("num_previous_programs", config.noema.num_previous_programs, value_type="int"),
        ],
        "overview": [
            field("summary", "Review the effective configuration before writing.", kind="readonly")
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


def refresh_dynamic_sections(
    walk: ConfigureWalk,
    config: AgentConfig,
    paths: ExamplePaths,
    output_dir: Path,
) -> None:
    """Rebuild visible fields while retaining inactive values in the session draft."""

    for section_fields in walk.sections.values():
        for field in section_fields:
            walk.draft_values[field["id"]] = field.get("value")
    walk.sections = _agent_sections(
        config,
        paths,
        output_dir,
        draft_values=walk.draft_values,
    )
    fields = walk.fields()
    walk.field_index = min(walk.field_index, max(0, len(fields) - 1))
    effective = deepcopy(config)
    effective, config_path, effective_output = _apply_walk_to_config(walk, effective)
    action = str(walk.draft_values.get("action", "write_and_run"))
    walk.sections["overview"][0]["value"] = render_config_overview(
        effective,
        config_path=config_path,
        output_dir=effective_output,
        action=action,
    )


def _parse_field_value(field: dict[str, Any]) -> Any:
    raw = field.get("value")
    value_type = field.get("value_type", "str")
    optional = value_type.startswith("optional_")
    base_type = value_type.removeprefix("optional_")
    if optional and raw in (None, ""):
        return None
    if base_type == "str":
        return "" if raw is None else str(raw)
    if base_type == "bool":
        if isinstance(raw, bool):
            return raw
        value = str(raw).strip().lower()
        if value not in ("true", "false"):
            raise ValueError(f"{field['id']} must be true or false, got {raw!r}")
        return value == "true"
    if raw is None:
        raise ValueError(f"{field['id']} may not be blank")
    if base_type == "int":
        return int(raw)
    if base_type == "float":
        return float(raw)
    if base_type in ("list", "dict"):
        parsed = raw if isinstance(raw, (list, dict)) else yaml.safe_load(str(raw))
        if base_type == "list" and isinstance(parsed, str):
            parsed = [item.strip() for item in parsed.split(",") if item.strip()]
        expected = list if base_type == "list" else dict
        if not isinstance(parsed, expected):
            raise ValueError(f"{field['id']} must be a {base_type}, got {raw!r}")
        return parsed
    raise ValueError(f"unsupported configure value type {value_type!r} for {field['id']}")


def _set_config_path(config: AgentConfig, path: str, value: Any) -> None:
    parts = path.split(".")
    agent_fields = {item.name for item in dataclass_fields(AgentConfig)}
    current: Any = config if parts[0] in agent_fields and parts[0] != "noema" else config.noema
    for part in parts[:-1]:
        current = getattr(current, part)
    attribute = parts[-1]
    annotation = get_type_hints(type(current)).get(attribute, Any)
    args = get_args(annotation)
    if get_origin(annotation) is Union and type(None) in args:
        annotation = next(arg for arg in args if arg is not type(None))
    if value is not None and (annotation is set or get_origin(annotation) is set):
        value = set(value)
    setattr(current, attribute, value)


def _apply_walk_to_config(
    walk: ConfigureWalk, config: AgentConfig
) -> tuple[AgentConfig, Path, Path]:
    """Push walk field values back onto config; return (config, config_path, output_dir)."""

    for section_fields in walk.sections.values():
        for field in section_fields:
            walk.draft_values[field["id"]] = field.get("value")
    fields_by_id = {
        field["id"]: field for section_fields in walk.sections.values() for field in section_fields
    }

    def _raw(field_id: str, fallback: Any = None) -> Any:
        field = fields_by_id.get(field_id)
        return fallback if field is None else field.get("value")

    module_raw = _raw("coordination.module", _raw("module", "null"))
    module = str(module_raw) if module_raw not in (None, "") else "null"
    config.noema.coordination.module = module

    for group_fields in advanced_field_groups(config, current_section=walk.section_id).values():
        for field in group_fields:
            if field["id"] in walk.draft_values and field["id"] not in fields_by_id:
                field["value"] = walk.draft_values[field["id"]]
                fields_by_id[field["id"]] = field

    agent_names = {item.name for item in dataclass_fields(AgentConfig)}
    noema_names = {item.name for item in dataclass_fields(type(config.noema))}
    for field_id, field in fields_by_id.items():
        if field.get("kind") == "readonly" or field_id.startswith("coordination.params."):
            continue
        if field_id in {
            "config",
            "programme",
            "evaluator",
            "output",
            "action",
            "summary",
            "module",
        }:
            continue
        first = field_id.split(".", 1)[0]
        if first not in agent_names and first not in noema_names:
            continue
        _set_config_path(config, field_id, _parse_field_value(field))

    specs = coordination_param_specs(module)
    writable = {spec.name: spec for spec in specs if spec.writable}
    params = {
        name: value for name, value in config.noema.coordination.params.items() if name in writable
    }
    for name, spec in writable.items():
        parameter_field = fields_by_id.get(spec.path)
        if parameter_field is not None:
            params[name] = _parse_field_value(parameter_field)
    config.noema.coordination.params = params

    if config.coordination_depth == "deep" and config.coordination_cli == AgentCliConfig():
        config.coordination_cli = AgentCliConfig(
            kind=config.mutation_cli.kind,
            model=config.mutation_cli.model,
        )

    cfg_name = _raw("config", "config.yaml") or "config.yaml"
    cwd = Path(_raw("programme", ".") or ".").resolve().parent
    if cfg_name == "(new) config.yaml":
        config_path = cwd / "config.yaml"
    else:
        config_path = cwd / cfg_name

    raw_out = _raw("output")
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
            if key in ("w", "W"):
                agent_config, config_path, output_dir = _apply_walk_to_config(walk, agent_config)
                run = True
                action_fields = walk.sections.get("write_and_run", [])
                action = action_fields[0].get("value") if action_fields else "write_and_run"
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
