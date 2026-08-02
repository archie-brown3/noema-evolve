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
from noema.agenthost.factory import create_agent_session
from noema.config import NoemaConfig


def build_agent_config(args: argparse.Namespace) -> AgentConfig:
    noema = NoemaConfig.from_yaml(args.config) if args.config else NoemaConfig()
    mutation_cli = AgentCliConfig(
        kind=args.mutation_cli,
        model=args.mutation_model,
    )
    coordination_cli = (
        copy.deepcopy(mutation_cli) if args.coordination_depth == "deep" else AgentCliConfig()
    )
    return AgentConfig(
        noema=noema,
        stop_children=args.stop_children,
        mutation_cli=mutation_cli,
        mutation_depth=args.mutation_depth,
        coordination_cli=coordination_cli,
        coordination_depth=args.coordination_depth,
    )


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
        default=os.environ.get("NOEMA_MUTATION_CLI", "opencode"),
        choices=("claude", "codex", "opencode", "agent"),
        help="Nested mutation CLI kind (also used for deep coordination CLI).",
    )
    parser.add_argument(
        "--mutation-model",
        default=os.environ.get("NOEMA_MUTATION_MODEL"),
        help="Optional model override for nested CLIs.",
    )
    parser.add_argument(
        "--mutation-depth",
        default="shallow",
        choices=("shallow", "deep"),
        help="Mutation CLI access: file-only (shallow) or inner MCP (deep).",
    )
    parser.add_argument(
        "--coordination-depth",
        default="shallow",
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
