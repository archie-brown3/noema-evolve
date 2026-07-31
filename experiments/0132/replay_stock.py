#!/usr/bin/env python3
"""Run one deterministic fixture through stock's ProcessParallelController."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from openevolve.config import (
    Config,
    DatabaseConfig,
    EvaluatorConfig,
    LLMConfig,
    LLMModelConfig,
    PromptConfig,
)
from openevolve.controller import OpenEvolve
from openevolve.llm.replay import create_replay_llm


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def state_summary(controller: OpenEvolve) -> dict:
    programs = list(controller.database.programs.values())
    return {
        "program_count": len(programs),
        "island_sizes": [len(island) for island in controller.database.islands],
        "programs": sorted(
            [
                {
                    "code": program.code,
                    "metrics": program.metrics,
                    "island": program.metadata.get("island", 0),
                    "has_parent": program.parent_id is not None,
                }
                for program in programs
            ],
            key=lambda item: (item["has_parent"], item["code"]),
        ),
    }


async def run(args: argparse.Namespace) -> dict:
    fixture_dir = Path(__file__).with_name("replay-fixtures")
    payload = json.loads(
        (fixture_dir / "scenarios.json").read_text(encoding="utf-8")
    )
    if args.scenario == "out_of_order":
        responses = payload["out_of_order"]["responses"]
        iterations = len(responses)
        workers = iterations
    else:
        responses = {"1": payload["scenarios"][args.scenario]}
        iterations = 1
        workers = 1
    replay_fixture = args.output_dir / "provider-fixture.json"
    replay_fixture.write_text(
        json.dumps({"responses": responses}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.environ["OPENEVOLVE_REPLAY_FIXTURE"] = str(replay_fixture)
    os.environ["OPENEVOLVE_ATTEMPT_LOG"] = str(
        args.output_dir / "attempt_trace.jsonl"
    )

    model = LLMModelConfig(
        name="task-0132-replay",
        init_client=create_replay_llm,
        weight=1.0,
        random_seed=42,
    )
    config = Config(
        max_iterations=iterations,
        checkpoint_interval=100,
        log_level="WARNING",
        random_seed=42,
        diff_based_evolution=False,
        max_code_length=300,
        llm=LLMConfig(
            models=[model],
            evaluator_models=[model],
            timeout=10,
            retries=0,
            retry_delay=0,
        ),
        prompt=PromptConfig(
            num_top_programs=1,
            num_diverse_programs=0,
            include_artifacts=False,
            use_template_stochasticity=False,
        ),
        database=DatabaseConfig(
            in_memory=True,
            num_islands=1,
            population_size=8,
            archive_size=4,
            random_seed=42,
            migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(
            cascade_evaluation=False,
            parallel_evaluations=workers,
            timeout=10,
            max_retries=0,
        ),
    )
    controller = OpenEvolve(
        initial_program_path=str(fixture_dir / "initial_program.py"),
        evaluation_file=str(fixture_dir / "evaluator.py"),
        config=config,
        output_dir=str(args.output_dir),
    )
    raised = None
    try:
        await controller.run(iterations=iterations)
    except Exception as exc:
        raised = f"{type(exc).__name__}: {exc}"
    return {
        "system": "stock_openevolve",
        "scenario": args.scenario,
        "requested_mutations": iterations,
        "raised": raised,
        "attempts": read_jsonl(args.output_dir / "attempt_trace.jsonl"),
        "state": state_summary(controller),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(args))
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
