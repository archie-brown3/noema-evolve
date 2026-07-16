"""
Run one noema coordination arm on bin_packing against a single inference node.

Usage:
    python run_noema_arm.py --arm null --api-base http://localhost:8090/v1 --output-dir noema_null_output

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

SYSTEM_MESSAGE = """You are an expert in online bin packing heuristics. Items of integer size arrive ONE AT A TIME and must be placed immediately into a bin of capacity 100 — you cannot see future items or reorder past ones. Your only job is to improve the `priority(item, bins)` heuristic: given the arriving item size and a numpy array of the remaining capacities of the bins that can still hold it, return a score per bin. The item is placed in the highest-scoring bin (or a new bin if none fits). The goal is to pack all items into as few bins as possible, beating the best-fit baseline.

Key facts about THIS problem:
- It is ONLINE. You cannot sort items or look ahead — the harness feeds items in arrival order and only calls your `priority` function. Any strategy that assumes offline access is impossible here.
- The baseline is best-fit: `priority = -(bins - item)` (prefer the tightest fit). Good heuristics are subtle non-linear functions of the item size and each bin's remaining capacity.
- Score = 1 / (1 + mean excess bins over the lower bound), averaged over the instances. Fewer bins → higher score.

Only the `priority` function (inside the EVOLVE-BLOCK) may change. The instance generator and the online placement loop are fixed and must not be touched.
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
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--model", default="/var/tmp/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf")
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--budget-tokens", type=int, default=1_000_000)  # From STUDY.md
    ap.add_argument("--retry-enabled", action="store_true", default=False)
    ap.add_argument("--retry-cap", type=int, default=2)
    args = ap.parse_args()

    with open(f"{EXAMPLE_DIR}/initial_program.py") as f:
        initial_program_code = f.read()

    config = NoemaConfig(
        max_iterations=args.iterations,
        checkpoint_interval=5,
        random_seed=args.seed,
        diff_based_evolution=True,
        retry_enabled=args.retry_enabled,
        retry_cap=args.retry_cap,
        num_inspirations=0,
        num_top_programs=1,
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
            include_artifacts=False,
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
        coordination=CoordinationConfig(module=args.arm),
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