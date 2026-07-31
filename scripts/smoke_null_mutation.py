#!/usr/bin/env python3
"""Smoke: one null-coordination mutation via a headless coding CLI.

Bootstraps ``AgentSession`` the same way the controller uses Noema seams
(select → advise → build_mutation_prompt → mutate → evaluate → store), with
``NullCoordination`` and ``CliMutationBackend(kind=...)``.

Usage:
  python scripts/smoke_null_mutation.py --cli opencode
  python scripts/smoke_null_mutation.py --cli opencode --example circle_packing --timeout 600

Env:
  NOEMA_MUTATION_CLI   default CLI kind when --cli omitted
  NOEMA_MUTATION_MODEL optional model override for the nested CLI
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

from noema.agenthost import CliMutationBackend, detect_available_mutation_cli
from noema.agenthost.config import agent_config_from_noema, AgentConfig, MutationCliConfig
from noema.agenthost.factory import create_agent_session
from noema.config import BudgetConfig, NoemaConfig
from noema.coordination import NullCoordination

REPO_ROOT = Path(__file__).resolve().parents[1]

TOY_INITIAL = textwrap.dedent(
    """\
    def f():
        return 1
    """
)

TOY_EVALUATOR = textwrap.dedent(
    """\
    import re

    def evaluate(program_path):
        with open(program_path) as f:
            code = f.read()
        m = re.search(r"return (\\d+(?:\\.\\d+)?)", code)
        if m is None:
            return {"error": "program returns no value"}
        return {"combined_score": min(1.0, float(m.group(1)) / 10.0)}
    """
)

CIRCLE_PACKING_TASK = textwrap.dedent(
    """\
    You are an expert mathematician specializing in circle packing problems and
    computational geometry. Improve the constructor that places 26 circles in a
    unit square to maximize the sum of their radii. AlphaEvolve achieved 2.635
    for n=26.

    Key geometric insights:
    - Circle packings often follow hexagonal patterns in the densest regions
    - Maximum density for infinite circle packing is pi/(2*sqrt(3)) ≈ 0.9069
    - Edge effects make square container packing harder than infinite packing
    - Circles can be placed in layers or shells when confined to a square
    - Varied radii often allow better space utilization than uniform radii

    Focus on designing an explicit constructor that places each circle in a
    specific position. Keep `run_packing()` and its return contract unchanged.
    Only code inside EVOLVE-BLOCK-START/END may change. Keep `compute_max_radii`
    mathematically correct (or leave it unmodified): each radius must satisfy
    the unit-square and non-overlap constraints.
    """
).strip()


def load_example(name: str) -> tuple[str, str, str, float]:
    """Return (initial_code, evaluator_path, task, eval_timeout_s)."""
    if name == "toy":
        return TOY_INITIAL, "", "Maximise the integer returned by f().", 30.0

    if name == "circle_packing":
        example = REPO_ROOT / "examples" / "circle_packing"
        initial = (example / "initial_program.py").read_text()
        evaluator = str(example / "evaluator.py")
        return initial, evaluator, CIRCLE_PACKING_TASK, 90.0

    raise SystemExit(f"unknown example {name!r}; expected toy or circle_packing")


def make_config(*, task: str, eval_timeout_s: float) -> NoemaConfig:
    return NoemaConfig(
        max_iterations=1,
        checkpoint_interval=100,
        language="python",
        # Full rewrite: coding CLIs edit the deliverable file in place.
        diff_based_evolution=False,
        max_code_length=80_000,
        prompt=PromptConfig(
            use_template_stochasticity=False,
            system_message=task,
        ),
        database=DatabaseConfig(
            in_memory=True,
            num_islands=1,
            population_size=20,
            random_seed=0,
            migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(
            cascade_evaluation=False,
            timeout=eval_timeout_s,
            max_retries=0,
        ),
        budget=BudgetConfig(total_tokens=1_000_000),
    )


async def run_once(
    *,
    cli: str,
    timeout_s: float,
    model: str | None,
    example: str,
) -> dict:
    initial, evaluator_src, task, eval_timeout = load_example(example)
    with tempfile.TemporaryDirectory(prefix="noema-mut-smoke-") as tmp:
        if example == "toy":
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(TOY_EVALUATOR)
        else:
            eval_path = evaluator_src

        out = os.path.join(tmp, "output")
        backend = CliMutationBackend(kind=cli, model=model)
        agent_cfg = agent_config_from_noema(
            make_config(task=task, eval_timeout_s=eval_timeout),
            kind=cli,
            model=model,
            timeout_s=timeout_s,
        )
        session = create_agent_session(
            agent_cfg,
            evaluation_file=eval_path,
            initial_program_code=initial,
            output_dir=out,
            coordination=NullCoordination(),
            mutation_backend=backend,
            task=task,
        )
        await session.begin_run()
        initial_prog = next(p for p in session.store.population() if p.id == "initial")
        print(
            f"example={example} initial_metrics={dict(initial_prog.metrics)} "
            f"code_chars={len(initial_prog.code)}"
        )
        session.next_target()
        parent = session.select_parent()
        brief = await session.get_brief()
        print(f"parent_id={parent['parent_id']} operator={parent['operator']}")
        print(f"prompt_user_chars={len(brief['brief'])} cli={cli}")
        result = await session.run_mutation(timeout_s=timeout_s)
        print(f"status={result.get('status')}")
        if result.get("status") == "accepted":
            print(f"program_id={result['program_id']} metrics={result['metrics']}")
        else:
            print(f"error={result.get('error')}")
            mut = result.get("mutation") or {}
            for key in ("stdout_log", "stderr_log", "deliverable", "wall_s", "exit_code"):
                if key in mut:
                    print(f"  {key}={mut[key]}")
            if mut.get("stderr_log") and os.path.isfile(mut["stderr_log"]):
                err = open(mut["stderr_log"]).read()[-3000:]
                if err.strip():
                    print("--- stderr (tail) ---")
                    print(err)
            if mut.get("stdout_log") and os.path.isfile(mut["stdout_log"]):
                out_txt = open(mut["stdout_log"]).read()[-3000:]
                if out_txt.strip():
                    print("--- stdout (tail) ---")
                    print(out_txt)

        durable = os.path.join("/tmp", f"noema-mut-smoke-{example}-{cli}")
        if os.path.isdir(out):
            if os.path.isdir(durable):
                shutil.rmtree(durable)
            shutil.copytree(out, durable)
            print(f"copied output to {durable}")
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cli",
        choices=("claude", "codex", "opencode"),
        default=os.environ.get("NOEMA_MUTATION_CLI") or "opencode",
        help="Headless mutation CLI (default: opencode)",
    )
    parser.add_argument(
        "--example",
        choices=("toy", "circle_packing"),
        default="toy",
        help="Initial program + evaluator pair",
    )
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument(
        "--model",
        default=os.environ.get("NOEMA_MUTATION_MODEL"),
        help="Optional model override for the nested CLI",
    )
    args = parser.parse_args()
    cli = args.cli or detect_available_mutation_cli()
    if not cli:
        print(
            "No supported mutation CLI found on PATH (claude, codex, opencode).",
            file=sys.stderr,
        )
        return 2
    timeout = args.timeout
    if timeout is None:
        timeout = 600.0 if args.example == "circle_packing" else 180.0
    print(
        f"using cli={cli} example={args.example} "
        f"timeout={timeout}s model={args.model!r}"
    )
    result = asyncio.run(
        run_once(
            cli=cli,
            timeout_s=timeout,
            model=args.model,
            example=args.example,
        )
    )
    return 0 if result.get("status") == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
