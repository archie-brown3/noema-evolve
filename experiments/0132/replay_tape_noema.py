#!/usr/bin/env python3
"""Run noema-null over the same deterministic response tape, zero network.

Two things make this the mirror of `replay_tape_stock.py` rather than merely a
similar script:

- the config is built by the layer-2 translator from the SAME stock YAML the
  stock arm runs, so the two arms cannot drift on a knob;
- the tape is injected through `NoemaController(mutation_llm=...)`, an existing
  test seam, so `noema/` is unmodified.

noema runs single-process and sequential, so the tape's own counter gives
request order directly — no per-worker reconstruction needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument  # noqa: E402
from stock_to_noema_config import noema_config_from_stock_yaml  # noqa: E402
from tape import Step, Tape, TapeChatClient, code_id, seed_uuid4  # noqa: E402


def _snapshot(database) -> Dict[str, Any]:
    # noema's store wraps openevolve's ProgramDatabase as `_db`; reach through so
    # the snapshot is the same object the stock side reads.
    inner = getattr(database, "_db", database)
    programs = list(inner.programs.values())
    scores = [
        p.metrics.get("combined_score")
        for p in programs
        if isinstance(p.metrics, dict) and isinstance(p.metrics.get("combined_score"), (int, float))
    ]
    return {
        "population": sorted(code_id(p.code) for p in programs),
        "island_sizes": [len(i) for i in getattr(inner, "islands", [])],
        "best": max(scores) if scores else None,
    }


async def run(
    tape_path: Path, stock_yaml: Path, output_dir: Path, iterations: int
) -> Dict[str, Any]:
    from noema.budget.ledger import TokenLedger
    from noema.budget.llm import BudgetedLLM
    from noema.controller import NoemaController

    # noema is single-process, so one patch covers both the initial program's ID
    # and every child's. Same seed as the stock parent: the two arms are compared
    # on content hashes, but keeping the streams aligned makes traces easier to
    # read side by side.
    seed_uuid4(1000)
    tape = Tape.from_json(tape_path)
    example = Path(__file__).resolve().parents[2] / ".openevolve-upstream/examples/circle_packing"

    config = noema_config_from_stock_yaml(
        stock_yaml, arm="null", substrate="islands", selection="stock_openevolve", seed=42
    )
    config.max_iterations = iterations
    config.diff_based_evolution = False  # full rewrite, as on the stock side
    config.checkpoint_interval = 10_000

    # One ledger shared by the controller and the tape client, so metering stays
    # consistent and no budget guard can end the run early and desynchronise the
    # arms. The budget is set far above what a short tape can spend.
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = TokenLedger(
        total_budget_tokens=10_000_000,
        log_path=str(output_dir / "llm_calls.jsonl"),
    )
    controller = NoemaController(
        config=config,
        evaluation_file=str(example / "evaluator.py"),
        initial_program_code=(example / "initial_program.py").read_text(encoding="utf-8"),
        output_dir=str(output_dir),
        ledger=ledger,
        mutation_llm=BudgetedLLM(
            model="0132-tape",
            ledger=ledger,
            account="mutation",
            tag="mutate",
            client=TapeChatClient(tape),
        ),
    )

    parents: List[str] = []
    admissions: List[Dict[str, Any]] = []
    database = controller.db
    log = instrument.EventLog()
    inner = instrument.install(database, log)
    log.emit(
        "post_construct",
        last_iteration=getattr(inner, "last_iteration", None),
        population_size_limit=inner.config.population_size,
        num_islands=len(getattr(inner, "islands", [])),
        **instrument.snapshot_ids(inner, code_id),
    )
    # noema routes parent choice through the substrate runtime, not through
    # openevolve's sample_from_island, so that is the seam to observe.
    real_select = controller.substrate.select
    real_add = database.add

    def select(*a, **kw):
        selection = real_select(*a, **kw)
        parents.append(code_id(selection.parent.code))
        return selection

    controller.substrate.select = select

    def add(program, *a, **kw):
        log.emit("add.enter", program=instrument.describe(inner, program, code_id),
                 args=[str(x) for x in a], kwargs={k: str(v) for k, v in kw.items()},
                 **instrument.snapshot_ids(inner, code_id))
        result = real_add(program, *a, **kw)
        log.emit("add.exit", returned=str(result), **instrument.snapshot_ids(inner, code_id))
        if getattr(program, "parent_id", None) is not None:
            admissions.append({"child": code_id(program.code), **_snapshot(database)})
        return result

    database.add = add

    raised = None
    try:
        await controller.run(iterations=iterations)
    except Exception as exc:
        raised = f"{type(exc).__name__}: {exc}"

    steps = [
        Step(
            index=i,
            parent=parents[i] if i < len(parents) else None,
            child=admissions[i]["child"] if i < len(admissions) else None,
            admitted=i < len(admissions),
            population=admissions[i]["population"] if i < len(admissions) else [],
            island_sizes=admissions[i]["island_sizes"] if i < len(admissions) else [],
            best=admissions[i]["best"] if i < len(admissions) else None,
        )
        for i in range(max(len(parents), len(admissions)))
    ]
    log.emit("final", **instrument.snapshot_ids(inner, code_id))
    log.write(output_dir / "events.jsonl")
    return {
        "system": "noema_null",
        "requested": iterations,
        "raised": raised,
        "steps": [s.__dict__ for s in steps],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", type=Path, required=True)
    ap.add_argument("--stock-yaml", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--iterations", type=int, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(args.tape, args.stock_yaml, args.output_dir, args.iterations))
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "steps"}, indent=2))
    print(f"{len(result['steps'])} steps -> {args.summary}")


if __name__ == "__main__":
    main()
