#!/usr/bin/env python3
"""Run stock OpenEvolve over a deterministic response tape, zero network.

Instrumentation lives here, in the harness — `.openevolve-upstream` stays
byte-identical to the 80945ed pin (layer 1). Two seams make that possible:

- `LLMModelConfig.init_client` (upstream config.py:60, consumed at
  llm/ensemble.py:25) supplies the tape-backed client;
- parent selection and admission both happen in the PARENT process
  (`process_parallel.py::_submit_iteration` calls `database.sample_from_island`),
  so wrapping the bound methods here observes them without touching the tree.

The worker subprocess gets its own Tape built from the tape file, so each entry
embeds its own index and the runner asserts response order == iteration order
rather than assuming it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import instrument  # noqa: E402
from tape import Step, Tape, code_id, seed_uuid4  # noqa: E402

TAPE_ENV = "NOEMA_0132_TAPE"

# Built once per process. In the worker this is a fresh instance whose counter
# advances in submission order (num_workers=1 keeps exactly one iteration in
# flight, so submission order is iteration order).
_WORKER_TAPE: Tape | None = None


def create_tape_client(config: Any):
    """Module-level so it survives pickling into the process pool."""
    global _WORKER_TAPE
    if _WORKER_TAPE is None:
        # Child program IDs are minted in the WORKER (process_parallel.py:291),
        # so the worker needs its own deterministic uuid4 — a patch applied only
        # in the parent would not reach it.
        seed_uuid4(2000)
        _WORKER_TAPE = Tape.from_json(os.environ[TAPE_ENV])
    from tape import make_stock_client

    return make_stock_client(_WORKER_TAPE)(config)


def _snapshot(database) -> Dict[str, Any]:
    programs = list(database.programs.values())
    scores = [
        p.metrics.get("combined_score")
        for p in programs
        if isinstance(p.metrics, dict) and isinstance(p.metrics.get("combined_score"), (int, float))
    ]
    return {
        "population": sorted(code_id(p.code) for p in programs),
        "island_sizes": [len(i) for i in database.islands],
        "best": max(scores) if scores else None,
    }


async def run(
    tape_path: Path, stock_yaml: Path, output_dir: Path, iterations: int
) -> Dict[str, Any]:
    from openevolve.config import Config, LLMModelConfig
    from openevolve.controller import OpenEvolve

    os.environ[TAPE_ENV] = str(tape_path)
    seed_uuid4(1000)  # parent process: the initial program's ID (controller.py:288)
    example = Path(__file__).resolve().parents[2] / ".openevolve-upstream/examples/circle_packing"

    # Both arms are configured from the SAME stock YAML — the stock side reads it
    # natively, the noema side through the layer-2 translator. Hardcoding either
    # side would let the arms differ on a knob and report that as a behavioural
    # divergence.
    config = Config.from_yaml(stock_yaml)
    config.max_iterations = iterations
    config.checkpoint_interval = 10_000  # no checkpoint writes during a tape run
    config.log_level = "WARNING"
    config.random_seed = 42
    config.diff_based_evolution = False  # full rewrite: child is parent-independent
    config.database.in_memory = True
    config.database.random_seed = 42
    # The stock YAML's db_path points at a persisted run directory; left set,
    # stock resumes that population and starts the tape with programs already in
    # it. The layer-2 translator drops db_path for noema, so the stock side must
    # drop it too or the two arms start from different populations.
    config.database.db_path = None
    config.evaluator.parallel_evaluations = 1  # Decision #72; fixes tape order
    config.evaluator.max_retries = 0

    model = LLMModelConfig(
        name="0132-tape", init_client=create_tape_client, weight=1.0, random_seed=42
    )
    config.llm.models = [model]
    config.llm.evaluator_models = [model]
    config.llm.retries = 0
    config.llm.retry_delay = 0

    controller = OpenEvolve(
        initial_program_path=str(example / "initial_program.py"),
        evaluation_file=str(example / "evaluator.py"),
        config=config,
        output_dir=str(output_dir),
    )

    parents: List[str] = []
    admissions: List[Dict[str, Any]] = []
    database = controller.database
    log = instrument.EventLog()
    inner = instrument.install(database, log)
    log.emit(
        "post_construct",
        last_iteration=database.last_iteration,
        population_size_limit=database.config.population_size,
        num_islands=len(database.islands),
        **instrument.snapshot_ids(inner, code_id),
    )
    real_sample = database.sample_from_island
    real_add = database.add

    def sample_from_island(*a, **kw):
        parent, inspirations = real_sample(*a, **kw)
        parents.append(code_id(parent.code))
        return parent, inspirations

    def add(program, *a, **kw):
        log.emit("add.enter", program=instrument.describe(inner, program, code_id),
                 args=[str(x) for x in a], kwargs={k: str(v) for k, v in kw.items()},
                 **instrument.snapshot_ids(inner, code_id))
        result = real_add(program, *a, **kw)
        log.emit("add.exit", returned=result, **instrument.snapshot_ids(inner, code_id))
        # The initial program is added before the loop starts and has no parent.
        if program.parent_id is not None:
            admissions.append({"child": code_id(program.code), **_snapshot(database)})
        return result

    database.sample_from_island = sample_from_island
    database.add = add

    raised = None
    try:
        await controller.run(iterations=iterations)
    except Exception as exc:  # a tape-exhaustion or evaluator error must be visible
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
        for i in range(len(parents))
    ]
    log.emit("final", **instrument.snapshot_ids(inner, code_id))
    log.write(output_dir / "events.jsonl")
    return {
        "system": "stock_openevolve",
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
