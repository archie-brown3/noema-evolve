"""Shared CLI bootstrap for agent host entry points (task 0176).

Parses argv, builds ``AgentConfig``, and constructs ``AgentSession``. No loop logic.
"""

from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
from typing import Optional

from noema.agenthost.config import AgentCliConfig, AgentConfig
from noema.agenthost.configure_files import load_noema_and_agent
from noema.agenthost.factory import create_agent_session


def build_agent_config(args: argparse.Namespace) -> AgentConfig:
    if args.config:
        config = load_noema_and_agent(args.config)
    else:
        config = AgentConfig()

    if args.stop_children is not None:
        config.stop_children = args.stop_children

    kind = args.mutation_cli
    model = args.mutation_model
    mutation_depth = args.mutation_depth
    coordination_depth = args.coordination_depth

    if not args.config:
        kind = kind or os.environ.get("NOEMA_MUTATION_CLI", "opencode")
        model = model if model is not None else os.environ.get("NOEMA_MUTATION_MODEL")
        mutation_depth = mutation_depth or "shallow"
        coordination_depth = coordination_depth or "shallow"
        config.mutation_cli = AgentCliConfig(kind=kind, model=model)
        config.mutation_depth = mutation_depth
        config.coordination_depth = coordination_depth
        config.coordination_cli = (
            copy.deepcopy(config.mutation_cli) if coordination_depth == "deep" else AgentCliConfig()
        )
        return config

    # With --config: YAML agent: is base; overlay only explicitly passed flags.
    if kind is not None:
        config.mutation_cli.kind = kind
    if model is not None:
        config.mutation_cli.model = model
    if mutation_depth is not None:
        config.mutation_depth = mutation_depth
    if coordination_depth is not None:
        config.coordination_depth = coordination_depth
    # --mutation-cli / --mutation-model also apply to deep coordination (see
    # --mutation-cli's help text) even when --coordination-depth is not
    # repeated on the command line and deep coordination came from the YAML.
    # Only kind/model are shared — coordination's own binary/extra_args/
    # timeout_s (if set in YAML) are transport details specific to that
    # seat and must not be clobbered by mutation_cli's values for them.
    if config.coordination_depth == "deep" and (
        coordination_depth == "deep" or kind is not None or model is not None
    ):
        config.coordination_cli.kind = config.mutation_cli.kind
        config.coordination_cli.model = config.mutation_cli.model
    return config


def parse_entry_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Noema agent host — load config and run evolution loop."
    )
    parser.add_argument("--config", type=Path, help="Noema YAML config (optional).")
    parser.add_argument(
        "--evaluation-file",
        type=Path,
        required=True,
        help="Python evaluator module path.",
    )
    parser.add_argument(
        "--initial-program",
        type=Path,
        required=True,
        help="Seed program file path.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Run artefacts directory.",
    )
    parser.add_argument(
        "--stop-children",
        type=int,
        default=None,
        help="Stop after this many accepted children.",
    )
    parser.add_argument(
        "--mutation-cli",
        default=None,
        choices=("claude", "codex", "opencode", "agent"),
        help="Nested mutation CLI kind (also used for deep coordination CLI).",
    )
    parser.add_argument(
        "--mutation-model",
        default=None,
        help="Optional model override for nested CLIs.",
    )
    parser.add_argument(
        "--mutation-depth",
        default=None,
        choices=("shallow", "deep"),
        help="Mutation CLI access: file-only (shallow) or inner MCP (deep).",
    )
    parser.add_argument(
        "--coordination-depth",
        default=None,
        choices=("shallow", "deep"),
        help="Coordination transport: BudgetedLLM (shallow) or CLI (deep).",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Task description when --config is omitted.",
    )
    return parser.parse_args(argv)


def create_session_from_args(args: argparse.Namespace):
    eval_path = str(args.evaluation_file.resolve())
    initial_code = args.initial_program.read_text()
    output_dir = str(args.output_dir.resolve())
    os.makedirs(output_dir, exist_ok=True)

    agent_cfg = build_agent_config(args)
    task = args.task
    if task is None and args.config:
        task = agent_cfg.noema.prompt.system_message
    if task is None:
        task = "Improve the program to maximise evaluator score."

    return create_agent_session(
        agent_cfg,
        evaluation_file=eval_path,
        initial_program_code=initial_code,
        output_dir=output_dir,
        task=task,
    )
