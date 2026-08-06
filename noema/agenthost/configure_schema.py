"""Field schema for the Noema agent-host configure UI."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Any, Literal, Optional, Union, get_args, get_origin, get_type_hints

import yaml

from noema.agenthost.config import AgentConfig


@dataclass(frozen=True)
class CoordinationParamSpec:
    """One explicitly supported ``coordination.params`` setting."""

    name: str
    value_type: str
    default: Any = None
    main: bool = False
    choices: tuple[str, ...] = ()
    writable: bool = True

    @property
    def path(self) -> str:
        return f"coordination.params.{self.name}"


COORDINATION_PARAM_SCHEMA: dict[str, tuple[CoordinationParamSpec, ...]] = {
    "null": (),
    "hifo": (
        CoordinationParamSpec("tips_per_prompt", "int", 3, main=True),
        CoordinationParamSpec(
            "tip_strategy",
            "str",
            "adaptive",
            main=True,
            choices=("random", "recent", "effective", "balanced", "adaptive"),
        ),
        CoordinationParamSpec("extraction_probability", "float", 0.8, main=True),
        CoordinationParamSpec("initial_tips", "optional_list", None, main=True),
        CoordinationParamSpec("failure_effectiveness", "float", -0.5),
        CoordinationParamSpec("max_code_chars", "int", 1000),
        CoordinationParamSpec("min_tip_length", "int", 10),
        CoordinationParamSpec("extraction_interval_offspring", "optional_int", 5),
        CoordinationParamSpec("extraction_min_population", "int", 3),
        CoordinationParamSpec("extraction_top_fraction", "float", 0.3),
        CoordinationParamSpec(
            "extraction_input",
            "str",
            "thoughts",
            choices=("thoughts", "thoughts+code"),
        ),
        CoordinationParamSpec("pool_max_size", "int", 30),
        CoordinationParamSpec("nav_history_cap", "int", 50),
    ),
    "bandit": (
        CoordinationParamSpec("operators", "list", ["e1", "e2", "m1", "m2", "m3"], main=True),
        CoordinationParamSpec("exploration_coef", "float", 1.0, main=True),
        CoordinationParamSpec("asymmetric", "bool", True, choices=("true", "false")),
        CoordinationParamSpec("adaptive_scale", "bool", True, choices=("true", "false")),
    ),
    "pe": (
        CoordinationParamSpec("interval", "int", 10, main=True),
        CoordinationParamSpec("n_clusters", "int", 3, main=True),
        CoordinationParamSpec("n_variants", "int", 3, main=True),
        CoordinationParamSpec("temperature", "float", 1.0, main=True),
        CoordinationParamSpec("domain_context", "str", ""),
        CoordinationParamSpec("language", "str", "python"),
    ),
    "pes-custom": (
        CoordinationParamSpec("executor_mode", "str", "advisory", main=True, writable=False),
        CoordinationParamSpec(
            "reflection_enabled",
            "bool",
            True,
            main=True,
            choices=("true", "false"),
        ),
        CoordinationParamSpec("context_window_tokens", "int", 16384, main=True),
        CoordinationParamSpec("max_siblings_rendered", "optional_int", 20, main=True),
        CoordinationParamSpec("max_code_chars", "int", 2000),
        CoordinationParamSpec("domain_context", "str", ""),
        CoordinationParamSpec("max_pending_reflections_per_tick", "optional_int", None),
        CoordinationParamSpec("reflection_slice_max_tokens", "int", 300),
        CoordinationParamSpec("strategy_digest_chars", "int", 150),
    ),
    "pes-faithful": (
        CoordinationParamSpec("executor_mode", "str", "directive", main=True, writable=False),
        CoordinationParamSpec(
            "reflection_enabled",
            "bool",
            True,
            main=True,
            choices=("true", "false"),
        ),
        CoordinationParamSpec("context_window_tokens", "int", 16384, main=True),
        CoordinationParamSpec("max_siblings_rendered", "optional_int", 20, main=True),
        CoordinationParamSpec("max_code_chars", "int", 2000),
        CoordinationParamSpec("domain_context", "str", ""),
        CoordinationParamSpec("max_pending_reflections_per_tick", "optional_int", None),
        CoordinationParamSpec("reflection_slice_max_tokens", "int", 300),
        CoordinationParamSpec("strategy_digest_chars", "int", 150),
    ),
    "reevo": (
        CoordinationParamSpec("function_name", "str", "evolvable", main=True),
        CoordinationParamSpec("func_desc", "str", "", main=True),
        CoordinationParamSpec("domain_context", "str", ""),
    ),
}


def coordination_param_specs(
    module: str, *, main: Optional[bool] = None
) -> tuple[CoordinationParamSpec, ...]:
    """Return known parameter specs for one coordination module."""

    specs = COORDINATION_PARAM_SCHEMA.get(module, ())
    if main is None:
        return specs
    return tuple(spec for spec in specs if spec.main is main)


def _serializable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _serializable_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serializable_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_serializable_value(item) for item in value)
    if isinstance(value, list):
        return [_serializable_value(item) for item in value]
    return value


def display_config_value(value: Any) -> str:
    """Format a config value for editing in one terminal field."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict, set, tuple)):
        return yaml.safe_dump(_serializable_value(value), default_flow_style=True).strip()
    return str(value)


def make_field(
    field_id: str,
    value: Any,
    *,
    value_type: str = "str",
    choices: tuple[str, ...] = (),
    kind: str = "open",
) -> dict[str, Any]:
    """Build the dict-shaped field consumed by ``ConfigureWalk``."""

    if choices:
        kind = "closed"
    result: dict[str, Any] = {
        "id": field_id,
        "kind": kind,
        "value_type": value_type,
        "value": display_config_value(value),
    }
    if choices:
        result["choices"] = list(choices)
    return result


def _config_path_details(config: AgentConfig, path: str) -> tuple[Any, Any]:
    parts = path.split(".")
    agent_fields = {item.name for item in dataclass_fields(AgentConfig)}
    current: Any = config if parts[0] in agent_fields and parts[0] != "noema" else config.noema
    annotation: Any = Any
    for part in parts:
        hints = get_type_hints(type(current))
        annotation = hints.get(part, Any)
        current = getattr(current, part)
    return current, annotation


def _value_type(annotation: Any, value: Any) -> str:
    optional = False
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Union and type(None) in args:
        optional = True
        annotation = next(arg for arg in args if arg is not type(None))
        origin = get_origin(annotation)
    if origin in (list, set, tuple) or annotation in (list, set, tuple):
        base = "list"
    elif origin is dict or annotation is dict:
        base = "dict"
    elif annotation is bool or isinstance(value, bool):
        base = "bool"
    elif annotation is int or isinstance(value, int):
        base = "int"
    elif annotation is float or isinstance(value, float):
        base = "float"
    else:
        base = "str"
    return f"optional_{base}" if optional else base


def _config_field(config: AgentConfig, path: str) -> dict[str, Any]:
    value, annotation = _config_path_details(config, path)
    choices: tuple[str, ...] = ()
    if get_origin(annotation) is Literal:
        choices = tuple(str(item) for item in get_args(annotation))
    elif _value_type(annotation, value).removeprefix("optional_") == "bool":
        choices = ("true", "false")
    elif path == "coordination_cli.kind":
        choices = ("claude", "codex", "opencode", "agent")
    elif path == "logging.level":
        choices = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    return make_field(
        path,
        value,
        value_type=_value_type(annotation, value),
        choices=choices,
    )


def advanced_field_groups(
    config: AgentConfig, *, current_section: str
) -> dict[str, list[dict[str, Any]]]:
    """Build the global advanced palette with the current page's group first."""

    paths_by_group = {
        "agent": (
            "host_log_verbosity",
            "mutation_cli.binary",
            "mutation_cli.extra_args",
            "coordination_cli.kind",
            "coordination_cli.binary",
            "coordination_cli.extra_args",
            "coordination_cli.timeout_s",
        ),
        "substrate": ("substrate.cvt_seed", "substrate.cvt_feature_bounds"),
        "selection": (
            "selection.stagnation_detection_enabled",
            "selection.stagnation_mode",
        ),
        "evolution": (
            "mutation_operators",
            "mutation_operator_seed",
            "prompt_metric_fields",
        ),
        "llm": tuple(
            f"llm.{role}.{item.name}"
            for role in ("mutation", "coordination")
            for item in dataclass_fields(type(getattr(config.noema.llm, role)))
            if item.name != "api_key"
        ),
        "budget": tuple(
            f"budget.{item.name}" for item in dataclass_fields(type(config.noema.budget))
        ),
        "logging": tuple(
            f"logging.{item.name}" for item in dataclass_fields(type(config.noema.logging))
        ),
        "representation": ("language", "file_suffix", "diff_pattern", "max_code_length"),
    }
    groups: dict[str, list[dict[str, Any]]] = {
        "agent": [_config_field(config, path) for path in paths_by_group["agent"]],
        "coordination": [
            make_field(
                spec.path,
                config.noema.coordination.params.get(spec.name, spec.default),
                value_type=spec.value_type,
                choices=spec.choices if spec.writable else (),
                kind="open" if spec.writable else "readonly",
            )
            for spec in coordination_param_specs(
                config.noema.coordination.module or "null", main=False
            )
        ],
    }
    for group in (
        "substrate",
        "selection",
        "evolution",
        "llm",
        "budget",
        "logging",
        "representation",
    ):
        groups[group] = [_config_field(config, path) for path in paths_by_group[group]]

    ordered = [
        "agent",
        "coordination",
        "substrate",
        "selection",
        "evolution",
        "llm",
        "budget",
        "logging",
        "representation",
    ]
    if current_section in ordered:
        ordered.remove(current_section)
        ordered.insert(0, current_section)
    return {name: groups[name] for name in ordered}
