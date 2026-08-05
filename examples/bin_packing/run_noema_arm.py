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

from dataclasses import replace

from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    EscalationConfig,
    LLMClientConfig,
    LLMRolesConfig,
    NoemaConfig,
    SelectionConfig,
    SubstrateConfig,
)
from noema.controller import NoemaController
from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SYSTEM_MESSAGE = """You are an expert in online bin packing heuristics. Items of integer size arrive ONE AT A TIME and must be placed immediately into a bin of capacity 100 — you cannot see future items or reorder past ones. Your only job is to improve the `priority(item, bins)` heuristic: given the arriving item size and a numpy array of the remaining capacities of the bins that can still hold it, return a score per bin. The item is placed in the highest-scoring bin (or a new bin if none fits). The goal is to pack all items into as few bins as possible, beating the best-fit baseline.

Key facts about THIS problem:
- It is ONLINE. You cannot sort items or look ahead — the harness feeds items in arrival order and only calls your `priority` function. Any strategy that assumes offline access is impossible here.
- The baseline is best-fit: `priority = -(bins - item)` (prefer the tightest fit). Good heuristics are subtle non-linear functions of the item size and each bin's remaining capacity.
- Score = 1 / (1 + mean excess bins over the lower bound), averaged over the instances. Fewer bins → higher score.

EVOLVE-BLOCK CONSTRAINT: Only modify code between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`. Do not touch any other function, import, constant, or comment — violations are rejected automatically and waste your budget.
CONCISENESS REQUIREMENT: Explain your mutation in at most one short sentence, then output your change immediately. No filler."""

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--arm",
        choices=["null", "hifo", "pes-custom", "pes-faithful", "pes", "bandit", "pe"],
        required=True,
        help="'pes' is a deprecated alias for pes-custom (task 0066)",
    )
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--api-key", default="none")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--substrate", choices=["islands", "tree", "cvt"], default="islands")
    # Default "substrate_default" resolves per-substrate (islands -> stock
    # OpenEvolve selection, tree -> UCT, cvt -> cvt_ucb) via
    # noema.substrates.registry.NATIVE_POLICIES; only pass this to override.
    ap.add_argument("--selection", default="substrate_default")
    ap.add_argument("--model", default="/var/tmp/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf")
    # Defaults to --model, i.e. one model for both seats, as before.
    ap.add_argument("--coordination-model", default=None)
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--budget-tokens", type=int, default=1_000_000)  # From STUDY.md
    ap.add_argument("--retry-enabled", action="store_true", default=False)
    ap.add_argument("--retry-cap", type=int, default=2)
    # Operator menu for the bandit arm. Comma-separated list of operator names
    # (e1,e2,m1,m2,m3). None (default) = legacy path, bandit hint ignored.
    ap.add_argument("--mutation-operators", default=None,
                    help="Comma-separated operator names, e.g. e1,e2,m1,m2,m3")
    ap.add_argument("--disable-reasoning", action="store_true", default=False,
                    help="Disable provider reasoning tokens (OpenRouter: reasoning={enabled:false})")
    ap.add_argument("--num-inspirations", type=int, default=3,
                    help="Elite programs shown as inspiration in mutation prompt (default 3)")
    ap.add_argument("--metric-fields", default="combined_score,bins_used,lower_bound",
                    help="Comma-separated metric fields to include in prompts (default: score+gap only)")
    # Model escalation (task 0107). Off unless --escalation-trigger is passed;
    # then a mutation burst routes to --escalation-model (defaults to the
    # coordination seat) when the trigger fires. Mutation seat only.
    ap.add_argument(
        "--escalation-trigger",
        choices=["plateau", "invalidity", "budget_fraction", "diversity", "random"],
        default=None,
    )
    ap.add_argument("--escalation-model", default=None)
    ap.add_argument("--escalation-burst", type=int, default=5)
    ap.add_argument("--escalation-cooldown", type=int, default=20)
    ap.add_argument("--escalation-window", type=int, default=10)
    ap.add_argument("--escalation-min-delta", type=float, default=0.001)
    ap.add_argument("--escalation-threshold", type=float, default=0.5)
    ap.add_argument("--escalation-fraction", type=float, default=0.7)
    ap.add_argument("--escalation-probability", type=float, default=0.2)
    args = ap.parse_args()

    with open(f"{EXAMPLE_DIR}/initial_program.py") as f:
        initial_program_code = f.read()

    mutation_llm = LLMClientConfig(
        model=args.model,
        api_base=args.api_base,
        api_key=args.api_key,
        temperature=0.7,
        top_p=0.95,
        max_tokens=4096,
        timeout=300,
        disable_reasoning=args.disable_reasoning,
    )

    mutation_operators = (
        [op.strip() for op in args.mutation_operators.split(",")]
        if args.mutation_operators else None
    )

    config = NoemaConfig(
        max_iterations=args.iterations,
        checkpoint_interval=5,
        random_seed=args.seed,
        diff_based_evolution=False,
        retry_enabled=args.retry_enabled,
        retry_cap=args.retry_cap,
        mutation_operators=mutation_operators,
        num_inspirations=args.num_inspirations,
        num_top_programs=3,
        num_previous_programs=3,
        prompt_metric_fields=set(args.metric_fields.split(",")) if args.metric_fields else None,
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
            num_diverse_programs=0,
            include_artifacts=False,
            system_message=SYSTEM_MESSAGE,
        ),
        budget=BudgetConfig(total_tokens=args.budget_tokens),
        substrate=SubstrateConfig(
            kind=args.substrate,
            # Gate 5: tighten CVT feature bounds for bin_packing's short
            # priority() functions. Default bounds (range_max_arg 0-500,
            # loop_nesting_max 0-3) map all short numpy one-liners to one
            # cell. These bounds match the actual variation space.
            cvt_feature_bounds={
                "math_operators": (0.0, 12.0),
                "loop_nesting_max": (0.0, 1.0),
                "comprehension_count": (0.0, 2.0),
                "range_max_arg": (0.0, 10.0),
            } if args.substrate == "cvt" else None,
        ),
        selection=SelectionConfig(policy=args.selection),
        llm=LLMRolesConfig(
            mutation=mutation_llm,
            coordination=replace(
                mutation_llm, model=args.coordination_model or args.model
            ),
        ),
        coordination=CoordinationConfig(
            module=args.arm,
            escalation=(
                EscalationConfig(
                    trigger=args.escalation_trigger,
                    escalation_model=args.escalation_model,
                    burst_length=args.escalation_burst,
                    cooldown_mutations=args.escalation_cooldown,
                    window=args.escalation_window,
                    min_delta=args.escalation_min_delta,
                    threshold=args.escalation_threshold,
                    fraction=args.escalation_fraction,
                    probability=args.escalation_probability,
                )
                if args.escalation_trigger
                else None
            ),
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