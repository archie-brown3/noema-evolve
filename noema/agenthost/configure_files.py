"""File-seam helpers for the agency configure CLI (task 0189).

Discover example cwd paths; load/save Noema YAML with top-level ``agent:``.
No UI. No new config type beyond ``NoemaConfig`` / ``AgentConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass(frozen=True)
class ExamplePaths:
    """Discovered paths for one example directory."""

    cwd: Path
    initial_program: Path
    evaluator: Path
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


def _load_yaml_mapping(path: Path) -> Any:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def discover_example(cwd: Path | str) -> ExamplePaths:
    """Require programme + evaluator in ``cwd``; list Noema-shaped ``*.yaml`` candidates."""

    root = Path(cwd).resolve()
    initial_program = root / "initial_program.py"
    evaluator = root / "evaluator.py"
    missing: list[str] = []
    if not initial_program.is_file():
        missing.append("initial_program.py")
    if not evaluator.is_file():
        missing.append("evaluator.py")
    if missing:
        raise ValueError(
            "example directory missing required file(s): "
            f"{', '.join(missing)}. Need initial_program.py and evaluator.py "
            f"in {root}"
        )

    raw = sorted(root.glob("*.yaml"), key=lambda p: p.name)
    candidates: list[Path] = []
    for path in raw:
        try:
            data = _load_yaml_mapping(path)
        except (OSError, yaml.YAMLError):
            continue
        if looks_like_openevolve_yaml(data):
            continue
        candidates.append(path.resolve())

    preferred: Optional[Path] = None
    candidate_names = {p.name: p for p in candidates}
    for name in ("config.yaml", "noema.yaml"):
        if name in candidate_names:
            preferred = candidate_names[name]
            break
    if preferred is None and len(candidates) == 1:
        preferred = candidates[0]

    new_config_path = (root / "config.yaml").resolve()
    return ExamplePaths(
        cwd=root,
        initial_program=initial_program,
        evaluator=evaluator,
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


def load_noema_and_agent(path: Path | str) -> "AgentConfig":
    """Load YAML: strip ``agent:``, build ``NoemaConfig`` + ``AgentConfig`` transport."""

    from noema.agenthost.config import AgentConfig
    from noema.config import NoemaConfig

    raw = _load_yaml_mapping(Path(path))
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    if looks_like_openevolve_yaml(raw):
        raise ValueError(
            f"refusing OpenEvolve-shaped config {path}; use a Noema YAML or (new) config.yaml"
        )
    agent_data = dict(raw.pop("agent", None) or {})
    noema = NoemaConfig.from_dict(raw)
    return AgentConfig(
        noema=noema,
        stop_children=agent_data.get("stop_children"),
        mutation_cli=_cli_from_mapping(agent_data.get("mutation_cli")),
        mutation_depth=agent_data.get("mutation_depth", "shallow"),
        coordination_cli=_cli_from_mapping(agent_data.get("coordination_cli")),
        coordination_depth=agent_data.get("coordination_depth", "shallow"),
        host_log_verbosity=agent_data.get("host_log_verbosity", "accepted"),
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
