#!/usr/bin/env python3
"""Run one deterministic task-0132 fixture through the real Noema controller."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    NoemaConfig,
    SelectionConfig,
    SubstrateConfig,
)
from noema.controller import NoemaController
from noema.coordination import NullCoordination


class FixtureClient:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.index = 0

        async def create(**params):
            item = self.responses[self.index]
            self.index += 1
            delay = float(item.get("delay_s", 0.0))
            if delay:
                await asyncio.sleep(delay)
            if item.get("error"):
                raise RuntimeError(str(item["error"]))
            content = str(item["content"])
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def state_summary(controller: NoemaController) -> dict:
    programs = list(controller.db._db.programs.values())
    return {
        "program_count": len(programs),
        "island_sizes": [
            len(controller.db._db.islands[index])
            for index in range(controller.config.database.num_islands)
        ],
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
        responses = list(payload["out_of_order"]["responses"].values())
        iterations = len(responses)
    else:
        responses = [payload["scenarios"][args.scenario]]
        iterations = 1

    config = NoemaConfig(
        max_iterations=iterations,
        checkpoint_interval=100,
        random_seed=42,
        diff_based_evolution=False,
        max_code_length=300,
        retry_enabled=False,
        num_inspirations=0,
        num_top_programs=1,
        num_previous_programs=1,
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
            parallel_evaluations=1,
            timeout=10,
            max_retries=0,
        ),
        prompt=PromptConfig(
            num_top_programs=1,
            num_diverse_programs=0,
            include_artifacts=False,
            use_template_stochasticity=False,
        ),
        budget=BudgetConfig(total_tokens=100_000),
        coordination=CoordinationConfig(module="null"),
        substrate=SubstrateConfig(kind="islands"),
        selection=SelectionConfig(policy="stock_openevolve"),
    )
    ledger = TokenLedger(
        total_budget_tokens=100_000,
        log_path=str(args.output_dir / "llm_calls.jsonl"),
    )
    mutation_llm = BudgetedLLM(
        model="task-0132-replay",
        ledger=ledger,
        account="mutation",
        tag="mutate",
        client=FixtureClient(responses),
        retries=0,
        retry_delay=0.0,
    )
    controller = NoemaController(
        config=config,
        evaluation_file=str(fixture_dir / "evaluator.py"),
        initial_program_code=(fixture_dir / "initial_program.py").read_text(
            encoding="utf-8"
        ),
        output_dir=str(args.output_dir),
        mutation_llm=mutation_llm,
        coordination=NullCoordination(),
        ledger=ledger,
    )
    raised = None
    try:
        await controller.run(iterations=iterations)
    except Exception as exc:  # provider fixture intentionally exercises this path
        raised = f"{type(exc).__name__}: {exc}"
    return {
        "system": "noema",
        "scenario": args.scenario,
        "requested_mutations": iterations,
        "raised": raised,
        "attempts": read_jsonl(args.output_dir / "attempt_trace.jsonl"),
        "selections": read_jsonl(args.output_dir / "selection_trace.jsonl"),
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
