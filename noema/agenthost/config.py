"""Agent-host transport configuration.

Scientific run settings stay in the canonical :class:`NoemaConfig`.  This
module contains only settings that differ at the agent-transport seam.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Literal, Optional

from noema.budget.cli_runner import SUPPORTED_MUTATION_CLIS
from noema.config import NoemaConfig


@dataclass
class AgentCliConfig:
    """How the host spawns a headless coding CLI."""

    kind: str = "opencode"
    binary: Optional[str] = None
    model: Optional[str] = None
    extra_args: list[str] = field(default_factory=list)
    timeout_s: float = 600.0


@dataclass
class AgentConfig:
    """Canonical Noema settings plus agent-only transport choices."""

    noema: NoemaConfig = field(default_factory=NoemaConfig)
    stop_children: Optional[int] = None
    mutation_cli: AgentCliConfig = field(default_factory=AgentCliConfig)
    mutation_depth: Literal["shallow", "deep"] = "shallow"
    coordination_cli: AgentCliConfig = field(default_factory=AgentCliConfig)
    coordination_depth: Literal["shallow", "deep"] = "shallow"
    # Compatibility seam for the monitor.  The CLI exposes one standard host
    # logging mode; older YAML spellings are normalised at the file boundary.
    host_log_verbosity: Literal["standard"] = "standard"

    def __post_init__(self) -> None:
        validate_agent_config(self)

    def resolved_stop_children(self) -> int:
        if self.stop_children is not None:
            return self.stop_children
        return self.noema.max_iterations


def validate_agent_config(config: AgentConfig) -> None:
    """Reject only invalid settings required by the selected transports."""

    if config.mutation_depth not in ("shallow", "deep"):
        raise ValueError(
            f"mutation_depth must be 'shallow' or 'deep', got {config.mutation_depth!r}"
        )
    if config.coordination_depth not in ("shallow", "deep"):
        raise ValueError(
            "coordination_depth must be 'shallow' or 'deep', " f"got {config.coordination_depth!r}"
        )
    if config.host_log_verbosity != "standard":
        raise ValueError(
            "host_log_verbosity must be 'standard', " f"got {config.host_log_verbosity!r}"
        )
    _validate_active_cli("mutation_cli", config.mutation_cli)
    if config.coordination_depth == "deep":
        _validate_active_cli("coordination_cli", config.coordination_cli)


def warn_agent_config_differences(
    config: AgentConfig,
    *,
    mutation_is_cli: bool,
) -> None:
    """Warn once per runtime construction about intentionally unsupported knobs."""

    warnings.warn(
        "NoemaAgent mutation uses an agent transport; noema.llm.mutation is ignored.",
        UserWarning,
        stacklevel=2,
    )
    if mutation_is_cli:
        warnings.warn(
            "NoemaAgent cannot meter coding-CLI mutation tokens; noema.budget "
            "applies only to in-process coordination calls.",
            UserWarning,
            stacklevel=2,
        )
    warnings.warn(
        "NoemaAgent does not support automatic checkpoints; "
        "noema.checkpoint_interval is ignored.",
        UserWarning,
        stacklevel=2,
    )
    if config.coordination_depth == "shallow" and config.coordination_cli != AgentCliConfig():
        warnings.warn(
            "coordination_cli is ignored while coordination_depth='shallow'.",
            UserWarning,
            stacklevel=2,
        )


def _validate_active_cli(name: str, cli: AgentCliConfig) -> None:
    if cli.kind not in SUPPORTED_MUTATION_CLIS:
        raise ValueError(f"{name}.kind must be one of {SUPPORTED_MUTATION_CLIS}, got {cli.kind!r}")
    if cli.timeout_s <= 0:
        raise ValueError(f"{name}.timeout_s must be positive, got {cli.timeout_s!r}")
