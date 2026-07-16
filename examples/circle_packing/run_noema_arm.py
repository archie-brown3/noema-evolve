"""
Run one noema coordination arm on circle_packing against a single inference node.

Usage:
    python run_noema_arm.py --arm null --api-base http://localhost:8090/v1 --output-dir noema_null_output
    python run_noema_arm.py --arm pes-custom   --api-base http://localhost:8091/v1 --output-dir noema_pes_output
    python run_noema_arm.py --arm pes-faithful --api-base http://localhost:8091/v1 --output-dir noema_pes_faithful_output

Both invocations must use the same --seed (default below) and the same model/
--iterations for the comparison to be meaningful: coordination.module is the
only thing that should differ between the two arms.
"""
import argparse
import asyncio
import logging
import os

from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    LLMClientConfig,
    NoemaConfig,
)
from noema.controller import NoemaController
from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SYSTEM_MESSAGE = """You are an expert mathematician specializing in circle packing problems and computational geometry. Your task is to improve a constructor function that directly produces a specific arrangement of 26 circles in a unit square, maximizing the sum of their radii. The AlphaEvolve paper achieved a sum of 2.635 for n=26.

Key geometric insights:
- Circle packings often follow hexagonal patterns in the densest regions
- Maximum density for infinite circle packing is pi/(2*sqrt(3)) ≈ 0.9069
- Edge effects make square container packing harder than infinite packing
- Circles can be placed in layers or shells when confined to a square
- Similar radius circles often form regular patterns, while varied radii allow better space utilization
- Perfect symmetry may not yield the optimal packing due to edge effects

Focus on designing an explicit constructor that places each circle in a specific position, rather than an iterative search algorithm.
IMPORTANT: Make sure that `compute_max_radii` is kept mathematically correct or left unmodified. The radius of any circle `i` MUST strictly satisfy `radii[i] <= min(x, y, 1 - x, 1 - y)` to stay inside the unit square, and `radii[i] + radii[j] <= dist` to avoid overlap. Any violations will result in a 0 validity score.
CONCISENESS REQUIREMENT: You must be extremely concise. Explain your proposed mutation in at most one short sentence, then output the SEARCH/REPLACE block immediately. Do not write any other conversational filler or explanations."""

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--arm",
        choices=["null", "hifo", "pes-custom", "pes-faithful", "pes", "bandit"],
        required=True,
        help="'pes' is a deprecated alias for pes-custom (task 0066)",
    )
    # The EoH operator menu (task 0027) is substrate: in a matrix cell it is ON
    # identically for every arm. The bandit REQUIRES it (it routes over the menu),
    # so it is auto-enabled for --arm bandit; pass this flag to also turn it on
    # for the other arms in a bandit-containing cell (they then draw operators at
    # random while the bandit steers them — the one controlled difference).
    ap.add_argument("--operator-menu", action="store_true", default=False)
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--model", default="/var/tmp/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf")
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--budget-tokens", type=int, default=2_000_000)
    ap.add_argument("--retry-enabled", action="store_true", default=False)
    ap.add_argument("--retry-cap", type=int, default=2)
    # The server's real context window. pes-faithful's reflection prompt is
    # pre-flight-checked against this; if it is smaller than the server's actual
    # n_ctx the guard refuses prompts that would in fact have fitted, which is
    # what killed the 2026-07-13 run (task 0067). Must match the served n_ctx.
    ap.add_argument("--context-window-tokens", type=int, default=16384)
    ap.add_argument("--retry-on", choices=["failure", "non_improvement"], default="failure")
    ap.add_argument("--num-inspirations", type=int, default=0)
    ap.add_argument("--num-top-programs", type=int, default=1)
    ap.add_argument("--include-artifacts", action="store_true", default=False)
    args = ap.parse_args()

    with open(f"{EXAMPLE_DIR}/initial_program.py") as f:
        initial_program_code = f.read()

    # Menu ON for the bandit (mandatory) or when explicitly requested for a
    # matched cell; otherwise None = the legacy diff-only path (unchanged).
    mutation_operators = (
        ["e1", "e2", "m1", "m2", "m3"]
        if (args.arm == "bandit" or args.operator_menu)
        else None
    )

    config = NoemaConfig(
        max_iterations=args.iterations,
        checkpoint_interval=5,
        random_seed=args.seed,
        diff_based_evolution=True,
        mutation_operators=mutation_operators,
        retry_enabled=args.retry_enabled,
        retry_cap=args.retry_cap,
        retry_on=args.retry_on,
        num_inspirations=args.num_inspirations,
        num_top_programs=args.num_top_programs,
        num_previous_programs=3,
        database=DatabaseConfig(
            population_size=60,
            archive_size=25,
            num_islands=4,
            elite_selection_ratio=0.3,
            exploitation_ratio=0.7,
            db_path=f"{args.output_dir}/db",
        ),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=60),
        prompt=PromptConfig(
            use_template_stochasticity=False,
            include_artifacts=args.include_artifacts,
            system_message=SYSTEM_MESSAGE,
        ),
        budget=BudgetConfig(total_tokens=args.budget_tokens),
        llm=LLMClientConfig(
            model=args.model,
            api_base=args.api_base,
            api_key="none",
            temperature=0.7,
            top_p=0.95,
            max_tokens=4096,
            timeout=300,
        ),
        coordination=CoordinationConfig(
            module=args.arm,
            params={"context_window_tokens": args.context_window_tokens},
        ),
    )

    controller = NoemaController(
        config=config,
        evaluation_file=f"{EXAMPLE_DIR}/evaluator.py",
        initial_program_code=initial_program_code,
        output_dir=args.output_dir,
    )
    best = asyncio.run(controller.run())
    print("BEST:", best.metrics if best else None)


if __name__ == "__main__":
    main()
