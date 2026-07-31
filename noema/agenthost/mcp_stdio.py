"""Stdio MCP entrypoint for the agent host (task 0160, pipeline Step 1).

Holds one ``AgentSession`` and forwards tool calls to ``mcp_server.dispatch``.
No evolution logic here — same path as ``tests/test_noema_agent_mcp_dispatch``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional

from noema.agenthost.config import AgentConfig, agent_config_from_noema
from noema.agenthost.factory import create_agent_session
from noema.agenthost.mcp_server import dispatch
from noema.config import NoemaConfig

INSTRUCTIONS = (
    "Noema agent host (Run 3). Call run_until_budget once; the host runs the "
    "full burst until stop_children and returns final run_status. Coordination "
    "(HiFo, PE, etc.) runs inside the host — do not call per-child tools for a "
    "full run. Six per-child tools remain for Run 2 debug only."
)


def _build_agent_config(args: argparse.Namespace) -> AgentConfig:
    overrides: dict = {"kind": args.mutation_cli}
    if args.mutation_model:
        overrides["model"] = args.mutation_model

    if args.config:
        agent_cfg = agent_config_from_noema(
            NoemaConfig.from_yaml(args.config),
            **overrides,
        )
    else:
        agent_cfg = AgentConfig()
        agent_cfg.mutation_cli.kind = args.mutation_cli
        if args.mutation_model:
            agent_cfg.mutation_cli.model = args.mutation_model

    if args.stop_children is not None:
        agent_cfg.stop_children = args.stop_children
        agent_cfg.max_iterations = args.stop_children

    return agent_cfg


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Noema agent host stdio MCP server (pipeline Step 1)."
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
        help="Nested mutation CLI kind.",
    )
    parser.add_argument(
        "--mutation-model",
        default=os.environ.get("NOEMA_MUTATION_MODEL"),
        help="Optional model override for the nested CLI.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Task description when --config is omitted.",
    )
    return parser.parse_args(argv)


def _create_session_from_args(args: argparse.Namespace):
    eval_path = str(args.evaluation_file.resolve())
    initial_code = args.initial_program.read_text()
    output_dir = str(args.output_dir.resolve())
    os.makedirs(output_dir, exist_ok=True)

    agent_cfg = _build_agent_config(args)
    task = args.task
    if task is None and args.config:
        task = agent_cfg.prompt.system_message
    if task is None:
        task = "Improve the program to maximise evaluator score."

    return create_agent_session(
        agent_cfg,
        evaluation_file=eval_path,
        initial_program_code=initial_code,
        output_dir=output_dir,
        task=task,
    )


def run_mcp(session) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise SystemExit(
            "MCP package not installed. Run: pip install -e '.[agent-host]'"
        ) from error

    mcp = FastMCP(
        "Noema Agent Host",
        instructions=INSTRUCTIONS,
        json_response=True,
    )

    @mcp.tool()
    async def begin_run() -> dict:
        """Evaluate the initial program and seed the population. Call once first."""
        return await dispatch(session, "begin_run")

    @mcp.tool()
    async def next_target() -> dict:
        """Open the next child target scope, or report run complete."""
        return await dispatch(session, "next_target")

    @mcp.tool()
    async def select_parent() -> dict:
        """Draw parent, inspirations, and mutation operator (host-owned)."""
        return await dispatch(session, "select_parent")

    @mcp.tool()
    async def get_brief() -> dict:
        """Fire coordination advice and assemble the mutation prompt."""
        return await dispatch(session, "get_brief")

    @mcp.tool()
    async def run_mutation(timeout_s: Optional[float] = None) -> dict:
        """Run headless mutation CLI, evaluate, and update the population."""
        if timeout_s is None:
            return await dispatch(session, "run_mutation")
        return await dispatch(session, "run_mutation", timeout_s=timeout_s)

    @mcp.tool()
    async def run_status() -> dict:
        """Read-only run counters and stop state."""
        return await dispatch(session, "run_status")

    @mcp.tool()
    async def run_until_budget() -> dict:
        """Run full burst until stop_children; returns final run_status."""
        return await dispatch(session, "run_until_budget")

    mcp.run()


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    session = _create_session_from_args(args)
    run_mcp(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
