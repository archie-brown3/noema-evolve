"""Agency configure CLI entry (task 0189): write gate + optional run.

Interactive section walk lives alongside; ``commit_and_maybe_run`` is the
testable write/run seam. Console script: ``noema``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from noema.agenthost.config import AgentConfig
from noema.agenthost.configure_files import (
    discover_example,
    load_noema_and_agent,
    save_noema_and_agent,
)
from noema.agenthost.factory import create_agent_session

Runner = Callable[[AgentConfig, Path, Path, Path], Any]


def _default_runner(
    agent_config: AgentConfig,
    evaluation_file: Path,
    initial_program: Path,
    output_dir: Path,
) -> Any:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    task = agent_config.noema.prompt.system_message or (
        "Improve the program to maximise evaluator score."
    )
    session = create_agent_session(
        agent_config,
        evaluation_file=str(Path(evaluation_file).resolve()),
        initial_program_code=Path(initial_program).read_text(),
        output_dir=str(output_dir.resolve()),
        task=task,
    )
    return asyncio.run(session.run_agent_mode())


def commit_and_maybe_run(
    *,
    config_path: Path,
    agent_config: AgentConfig,
    evaluation_file: Path,
    initial_program: Path,
    output_dir: Path,
    run: bool,
    runner: Optional[Runner] = None,
) -> dict[str, Any]:
    """Write YAML (with ``agent:``), optionally start the agent host."""

    config_path = Path(config_path)
    save_noema_and_agent(config_path, agent_config)
    if not run:
        return {"wrote": str(config_path.resolve()), "ran": False}
    run_fn = runner or _default_runner
    status = run_fn(
        agent_config,
        Path(evaluation_file).resolve(),
        Path(initial_program).resolve(),
        Path(output_dir).resolve(),
    )
    return {"wrote": str(config_path.resolve()), "ran": True, "status": status}


def main(argv: Optional[list[str]] = None) -> int:
    """``noema`` console entry: configure in cwd, then optional run."""

    parser = argparse.ArgumentParser(
        prog="noema",
        description="Configure and optionally run a Noema agency example in cwd.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="After writing config, run the agent host without prompting.",
    )
    parser.add_argument(
        "--write-only",
        action="store_true",
        help="Write config and exit (no run).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Artefact directory (default: ./noema_agent_output).",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    try:
        paths = discover_example(cwd)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if paths.preferred_config is not None:
        config_path = paths.preferred_config
        agent_config = load_noema_and_agent(config_path)
    else:
        config_path = paths.new_config_path or (cwd / "config.yaml")
        agent_config = AgentConfig()

    # Upstream's rule (controller.py): the seed programme's extension sets
    # file_suffix unless the YAML already names a non-default one.
    if agent_config.noema.file_suffix == ".py":
        agent_config.noema.file_suffix = paths.file_suffix

    output_dir = args.output_dir or (cwd / "noema_agent_output")

    if not args.write_only and sys.stdin.isatty():
        from noema.agenthost.monitor import run_noema_app

        outcome = run_noema_app(
            paths=paths,
            config_path=config_path,
            agent_config=agent_config,
            output_dir=output_dir,
            run_immediately=args.yes,
        )
        if outcome.wrote is not None:
            print(f"wrote {outcome.wrote}")
        if outcome.ran and outcome.status is not None:
            print(outcome.status)
        if outcome.error:
            print(outcome.error, file=sys.stderr)
        return outcome.exit_code

    run = bool(args.yes) and not args.write_only
    result = commit_and_maybe_run(
        config_path=config_path,
        agent_config=agent_config,
        evaluation_file=paths.evaluator,
        initial_program=paths.initial_program,
        output_dir=output_dir,
        run=run,
    )
    print(f"wrote {result['wrote']}")
    if result["ran"]:
        print(result.get("status"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
