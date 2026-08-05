"""File-seam helpers for the agency configure CLI (task 0189).

Discover example cwd paths; load/save Noema YAML with top-level ``agent:``.
No UI. No new config type beyond ``NoemaConfig`` / ``AgentConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

import yaml
from openevolve.utils.code_utils import extract_code_language  # type: ignore[import-untyped]


@dataclass(frozen=True)
class ExamplePaths:
    """Discovered paths for one example directory."""

    cwd: Path
    initial_program: Path
    evaluator: Path
    file_suffix: str = ".py"
    language: str = "python"
    config_candidates: tuple[Path, ...] = ()
    preferred_config: Optional[Path] = None
    use_skeleton: bool = False
    new_config_path: Optional[Path] = None


def looks_like_openevolve_yaml(data: Any) -> bool:
    """Structural tells for OpenEvolve configs that must not feed NoemaConfig."""

    if not isinstance(data, dict):
        return True
    if "log_level" in data:
        return True
    llm = data.get("llm")
    if isinstance(llm, dict) and "primary_model" in llm:
        return True
    prompt = data.get("prompt")
    if isinstance(prompt, dict) and prompt.get("use_template_stochasticity") is True:
        return True
    return False


def _has_noema_config_tell(data: dict[str, Any]) -> bool:
    """True when a mapping carries at least one key only a Noema config has."""

    from noema.config import NoemaConfig

    known = {field.name for field in fields(NoemaConfig)} | {"agent"}
    return any(key in known for key in data)


def _load_yaml_mapping(path: Path) -> Any:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def discover_example(cwd: Path | str) -> ExamplePaths:
    """Require programme + evaluator; list loadable YAML config paths in ``cwd``."""

    root = Path(cwd).resolve()
    # The seed programme may be in any language (upstream reads the extension
    # off the discovered path); the evaluator is loaded as a Python module, so
    # it stays evaluator.py.
    seeds = sorted((p for p in root.glob("initial_program.*") if p.is_file()), key=lambda p: p.name)
    if len(seeds) > 1:
        raise ValueError(
            "example directory has more than one seed programme: "
            f"{', '.join(p.name for p in seeds)}. Keep exactly one "
            f"initial_program.<ext> in {root}"
        )
    evaluator = root / "evaluator.py"
    missing: list[str] = []
    if not seeds:
        missing.append("initial_program.<ext>")
    if not evaluator.is_file():
        missing.append("evaluator.py")
    if missing:
        raise ValueError(
            "example directory missing required file(s): "
            f"{', '.join(missing)}. Need initial_program.<ext> and evaluator.py "
            f"in {root}"
        )
    initial_program = seeds[0]

    raw = sorted(root.glob("*.yaml"), key=lambda p: p.name)
    candidates: list[Path] = []
    for path in raw:
        try:
            data = _load_yaml_mapping(path)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        candidates.append(path.resolve())

    preferred: Optional[Path] = None
    candidate_names = {p.name: p for p in candidates}
    for name in ("config.yaml", "noema.yaml"):
        if name in candidate_names:
            preferred = candidate_names[name]
            break
    if preferred is None and len(candidates) == 1:
        sole = candidates[0]
        # A clean load is not evidence of a config: _normalise_openevolve_yaml
        # drops every key it does not recognise, so an unrelated mapping loads
        # fine and would be adopted as the run config *and* rewritten in place
        # (task 0201). Adopt an unconventionally named file only on a positive
        # tell that it really is one.
        if _has_noema_config_tell(_load_yaml_mapping(sole)):
            preferred = sole

    new_config_path = (root / "config.yaml").resolve()
    return ExamplePaths(
        cwd=root,
        initial_program=initial_program,
        evaluator=evaluator,
        file_suffix=initial_program.suffix,
        language=extract_code_language(initial_program.read_text()),
        config_candidates=tuple(candidates),
        preferred_config=preferred,
        use_skeleton=len(candidates) == 0,
        new_config_path=new_config_path,
    )


def _cli_from_mapping(data: Optional[dict]) -> "AgentCliConfig":
    from noema.agenthost.config import AgentCliConfig

    if not data:
        return AgentCliConfig()
    return AgentCliConfig(
        kind=data.get("kind", "opencode"),
        binary=data.get("binary"),
        model=data.get("model"),
        extra_args=list(data.get("extra_args") or []),
        timeout_s=float(data.get("timeout_s", 600.0)),
    )


def _host_log_verbosity(value: Any) -> str:
    """Map legacy monitor values onto the single standard host-log mode."""

    if value in (None, "", "accepted", "full", "normal", "debug"):
        return "standard"
    return value


def _normalise_openevolve_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    """Adapt the older OpenEvolve-shaped example YAML to Noema's schema."""

    from noema.config import LLMClientConfig, NoemaConfig

    noema_fields = {field.name for field in fields(NoemaConfig)}
    sections = {"database", "evaluator", "prompt", "llm", "logging"}
    data = {key: value for key, value in raw.items() if key in noema_fields and key not in sections}
    if data.get("random_seed") is None:
        # Noema's seed is an integer because it derives deterministic child
        # streams from it.  The legacy OpenEvolve null means "unspecified";
        # use Noema's canonical default rather than failing during load.
        data.pop("random_seed", None)

    prompt = dict(raw.get("prompt") or {})
    # Noema deliberately rejects stochastic prompt templates so the mutation
    # prompt remains identical across arms.
    prompt["use_template_stochasticity"] = False
    data["prompt"] = prompt
    if "num_top_programs" not in data and "num_top_programs" in prompt:
        data["num_top_programs"] = prompt["num_top_programs"]

    data["database"] = dict(raw.get("database") or {})
    if raw.get("random_seed") is not None:
        data["database"].setdefault("random_seed", raw["random_seed"])
    data["evaluator"] = dict(raw.get("evaluator") or {})

    llm = dict(raw.get("llm") or {})
    client_fields = {field.name for field in fields(LLMClientConfig)}

    def client(model: Any) -> dict[str, Any]:
        result = {key: llm[key] for key in client_fields if key in llm}
        if model is not None:
            result["model"] = model
        return result

    primary_model = llm.get("primary_model", llm.get("model"))
    secondary_model = llm.get("secondary_model", primary_model)
    data["llm"] = {
        "mutation": client(primary_model),
        "coordination": client(secondary_model),
    }
    if "log_level" in raw:
        data["logging"] = {"level": raw["log_level"]}
    return data


def load_noema_and_agent(path: Path | str) -> "AgentConfig":
    """Load Noema or legacy OpenEvolve YAML into canonical agent state."""

    from noema.agenthost.config import AgentConfig
    from noema.config import NoemaConfig

    raw = _load_yaml_mapping(Path(path))
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    if looks_like_openevolve_yaml(raw):
        raw = _normalise_openevolve_yaml(raw)
    agent_data = dict(raw.pop("agent", None) or {})
    noema = NoemaConfig.from_dict(raw)
    return AgentConfig(
        noema=noema,
        stop_children=agent_data.get("stop_children"),
        mutation_cli=_cli_from_mapping(agent_data.get("mutation_cli")),
        mutation_depth=agent_data.get("mutation_depth", "shallow"),
        coordination_cli=_cli_from_mapping(agent_data.get("coordination_cli")),
        coordination_depth=agent_data.get("coordination_depth", "shallow"),
        host_log_verbosity=_host_log_verbosity(agent_data.get("host_log_verbosity")),
    )


def _cli_to_mapping(cli: "AgentCliConfig") -> dict[str, Any]:
    return {
        "kind": cli.kind,
        "binary": cli.binary,
        "model": cli.model,
        "extra_args": list(cli.extra_args),
        "timeout_s": cli.timeout_s,
    }


def save_noema_and_agent(path: Path | str, config: "AgentConfig") -> None:
    """Write Noema science keys plus top-level ``agent:`` transport block."""

    from noema.agenthost.config import AgentConfig

    if not isinstance(config, AgentConfig):
        raise TypeError(f"expected AgentConfig, got {type(config)!r}")
    data = config.noema.to_dict()
    # YAML can't represent set; NoemaConfig.to_dict may include prompt_metric_fields.
    pmf = data.get("prompt_metric_fields")
    if isinstance(pmf, set):
        data["prompt_metric_fields"] = sorted(pmf) if pmf else None
    data["agent"] = {
        "stop_children": config.stop_children,
        "mutation_depth": config.mutation_depth,
        "coordination_depth": config.coordination_depth,
        "host_log_verbosity": config.host_log_verbosity,
        "mutation_cli": _cli_to_mapping(config.mutation_cli),
        "coordination_cli": _cli_to_mapping(config.coordination_cli),
    }
    Path(path).write_text(yaml.safe_dump(data, sort_keys=True))
