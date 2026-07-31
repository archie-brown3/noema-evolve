"""
Run one noema coordination arm on the MLIR attention optimization benchmark.
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
    SelectionConfig,
    SubstrateConfig,
)
from noema.controller import NoemaController
from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SYSTEM_MESSAGE = """You are an expert MLIR compiler optimization specialist. Your task is to improve MLIR transformation parameters for attention kernels to maximize speedup.

Key optimization targets:
- tile_size_m: 16, 32, 64, 128 — sequence dimension tile size. Larger tiles = more parallelism but more cache pressure.
- tile_size_n: 32, 64, 128, 256 — head dimension tile size. Match to hardware vector width.
- vectorization: none, affine, linalg — linalg gives best SIMD utilization.
- unroll_factor: 1, 2, 4, 8 — higher = more ILP but larger code.
- fusion_strategy: none, producer, consumer, both — both maximizes memory reduction for Q@K^T + softmax + @V.
- loop_interchange: True/False — reorder loops for cache-friendly access.
- use_shared_memory: True/False — GPU shared memory for intermediate tensors.
- optimize_for_latency: True/False — True for inference, False for throughput.
- enable_blocking: True/False — block-wise computation like FlashAttention.
- enable_recomputation: True/False — recompute instead of storing, saves memory.

Published targets: 1.32x speedup (AlphaEvolve level). Above 1.0x is progress. Above 1.15x is good. Above 1.25x is excellent.

Focus on making targeted parameter changes that increase the speedup metric. Output a SEARCH/REPLACE block that modifies specific parameter values."""

EXAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--arm",
        choices=["null", "hifo", "pes-custom", "pes-faithful", "pes"],
        required=True,
    )
    ap.add_argument("--api-base", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--model", default="deepseek/deepseek-v4-flash")
    ap.add_argument("--iterations", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--budget-tokens", type=int, default=500_000)
    ap.add_argument("--retry-enabled", action="store_true", default=False)
    ap.add_argument("--retry-cap", type=int, default=2)
    ap.add_argument("--context-window-tokens", type=int, default=16384)
    ap.add_argument("--retry-on", choices=["failure", "non_improvement"], default="failure")
    ap.add_argument("--num-inspirations", type=int, default=0)
    ap.add_argument("--num-top-programs", type=int, default=1)
    ap.add_argument("--include-artifacts", action="store_true", default=False)
    ap.add_argument("--substrate", choices=["islands", "tree"], default="islands")
    ap.add_argument(
        "--selection-policy",
        choices=["substrate_default", "stock_openevolve", "boltzmann", "uct"],
        default="substrate_default",
    )
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--api-key-env", default=None)
    args = ap.parse_args()

    api_key = "none"
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env) or ""
        if not api_key:
            raise SystemExit(f"--api-key-env {args.api_key_env}: variable is empty or unset")

    os.makedirs(args.output_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(args.output_dir, "run.log"))
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(file_handler)

    with open(f"{EXAMPLE_DIR}/initial_program_noema.py") as f:
        initial_program_code = f.read()

    config = NoemaConfig(
        max_iterations=args.iterations,
        checkpoint_interval=5,
        random_seed=args.seed,
        diff_based_evolution=True,
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
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=120),
        prompt=PromptConfig(
            use_template_stochasticity=False,
            include_artifacts=args.include_artifacts,
            system_message=SYSTEM_MESSAGE,
        ),
        budget=BudgetConfig(total_tokens=args.budget_tokens),
        llm=LLMClientConfig(
            model=args.model,
            api_base=args.api_base,
            api_key=api_key,
            temperature=args.temperature,
            top_p=0.95,
            max_tokens=4096,
            timeout=300,
        ),
        coordination=CoordinationConfig(
            module=args.arm,
            params={"context_window_tokens": args.context_window_tokens},
        ),
        substrate=SubstrateConfig(kind=args.substrate),
        selection=SelectionConfig(policy=args.selection_policy),
    )

    controller = NoemaController(
        config=config,
        evaluation_file=f"{EXAMPLE_DIR}/evaluator.py",
        initial_program_code=initial_program_code,
        output_dir=args.output_dir,
    )
    try:
        best = asyncio.run(controller.run())
    except Exception:
        logging.getLogger(__name__).exception("run crashed")
        raise
    print("BEST:", best.metrics if best else None)


if __name__ == "__main__":
    main()