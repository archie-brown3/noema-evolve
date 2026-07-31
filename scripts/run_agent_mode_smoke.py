#!/usr/bin/env python3
"""Live burst smoke: run_agent_mode + HiFo verbal gradients + nested mutation CLI.

Usage:
  python scripts/run_agent_mode_smoke.py --cli agent --example function_minimization -v
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

from noema.agenthost.config import (
    AgentCoordinationConfig,
    AgentConfig,
    AgentLLMConfig,
    MutationCliConfig,
)
from noema.agenthost.factory import create_agent_session
from noema.agenthost.mutation import CliMutationBackend, MutationRequest
from noema.budget.ledger import COORDINATION_ACCOUNT, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.coordination import build_coordination_module
from noema.config import BudgetConfig, SubstrateConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
STOCK = REPO_ROOT / ".openevolve-stock" / "examples" / "function_minimization"

LOG = logging.getLogger("run_agent_mode_smoke")

HIFO_IMMEDIATE_PARAMS = {
    "tips_per_prompt": 3,
    "extraction_probability": 1.0,
    "extraction_interval_offspring": 1,
    "extraction_min_population": 2,
}


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    for name in (
        "noema.agenthost",
        "noema.coordination",
        "noema.coordination.hifo",
        "run_agent_mode_smoke",
    ):
        logging.getLogger(name).setLevel(level)


def vlog_block(title: str, body: str, max_chars: int = 4000) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    text = body if len(body) <= max_chars else body[:max_chars] + "\n... [truncated]"
    print(text)
    print("=" * 72)


def stub_coordination_llm(ledger: TokenLedger) -> BudgetedLLM:
    """Stub HiFo extraction; nested agent harness owns real mutation LLM calls."""

    async def create(**params):
        LOG.info("HiFo extraction LLM call (stub) — returning synthetic insight lines")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            "- Prefer gradient-aware local steps with occasional "
                            "global restarts when progress stalls.\n"
                            "- Use adaptive step sizes that shrink near suspected minima."
                        )
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=20),
        )

    return BudgetedLLM(
        model="smoke-stub",
        ledger=ledger,
        account=COORDINATION_ACCOUNT,
        tag="hifo.smoke.coordination",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        retries=0,
        retry_delay=0.0,
    )


def build_coordination(arm: str, ledger: TokenLedger, seed: int):
    if arm != "hifo":
        raise ValueError(f"smoke script only supports hifo today, got {arm!r}")
    return build_coordination_module(
        arm,
        dict(HIFO_IMMEDIATE_PARAMS),
        llm=stub_coordination_llm(ledger),
        rng=random.Random(seed),
    )


def load_function_minimization() -> tuple[str, str, str, float]:
    initial = (STOCK / "initial_program.py").read_text()
    evaluator = str(STOCK / "evaluator.py")
    task = textwrap.dedent(
        """\
        You are an expert programmer specializing in optimization algorithms. Your task is to
        improve a function minimization algorithm to find the global minimum of a complex
        function with many local minima. The function is f(x, y) = sin(x) * cos(y) + sin(x*y)
        + (x^2 + y^2)/20. Focus on improving the search_algorithm function to reliably find
        the global minimum, escaping local minima that might trap simple algorithms."""
    ).strip()
    return initial, evaluator, task, 90.0


def build_config(
    *,
    cli: str,
    arm: str,
    stop_children: int,
    timeout_s: float,
    model: str | None,
    output_dir: str,
) -> AgentConfig:
    initial, evaluator, task, eval_timeout = load_function_minimization()
    _ = evaluator
    return AgentConfig(
        stop_children=stop_children,
        max_iterations=stop_children,
        checkpoint_interval=100,
        random_seed=42,
        language="python",
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
            random_seed=42,
            migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(
            cascade_evaluation=False,
            timeout=eval_timeout,
            max_retries=0,
        ),
        budget=BudgetConfig(total_tokens=1_000_000),
        substrate=SubstrateConfig(kind="flat", steps_per_generation=1),
        coordination=AgentCoordinationConfig(
            module=arm,
            params=dict(HIFO_IMMEDIATE_PARAMS) if arm == "hifo" else {},
        ),
        llm=AgentLLMConfig(),
        mutation_cli=MutationCliConfig(
            kind=cli,
            model=model,
            timeout_s=timeout_s,
        ),
    )


def wrap_mutation_backend_verbose(backend, mutation_timeout: float):
    if not isinstance(backend, CliMutationBackend):
        return
    orig_run = backend.run

    def verbose_run(request: MutationRequest):
        from noema.agenthost.cli_backends import build_cli_user_message, build_mutation_cli_command

        layout = request.layout
        work = layout.work_dir if layout else request.work_dir
        deliverable = layout.deliverable_path if layout else request.deliverable_path
        parent_path = layout.parent_path if layout else work / "parent.py"
        system_path = layout.system_path if layout else work / "SYSTEM.md"
        cli_user = build_cli_user_message(
            request.prompt,
            deliverable=deliverable,
            parent_path=parent_path,
        )
        try:
            argv = build_mutation_cli_command(
                backend.kind,
                work_dir=work,
                system_path=system_path,
                user_message=cli_user,
                binary=backend.binary,
                model=backend.model,
                extra_args=backend.extra_args,
            )
            LOG.info("spawning nested mutation CLI: %s", " ".join(argv[:8]) + (" ..." if len(argv) > 8 else ""))
            LOG.debug("full argv: %s", argv)
            LOG.info("mutation work_dir=%s deliverable=%s timeout=%ss", work, deliverable, mutation_timeout)
        except Exception as exc:
            LOG.warning("could not preview mutation argv: %s", exc)
        if "# Coordination Guidance" in request.prompt.get("user", ""):
            vlog_block(
                "MUTATION PROMPT (user) — includes HiFo verbal-gradient block",
                request.prompt.get("user", ""),
            )
        return orig_run(request)

    backend.run = verbose_run


async def run_agent_mode_verbose(session, mutation_timeout: float) -> dict:
    """Mirror session.run_agent_mode with step-by-step logging."""
    seat = session._seat
    runner = session._runner
    orig_run_mutation = runner.run_mutation
    burst_cap = session.substrate.steps_per_generation

    async def run_mutation_logged(timeout_s=None):
        LOG.info("--- run_mutation (nested CLI) ---")
        return await orig_run_mutation(
            timeout_s=timeout_s if timeout_s is not None else mutation_timeout
        )

    runner.run_mutation = run_mutation_logged

    await session.begin_run()
    LOG.info("begin_run: %s", session.run_status())

    while session.children_accepted < session.stop_children:
        burst_n = 0
        LOG.info(
            "burst window: cap=%d children_accepted=%d stop=%d",
            burst_cap,
            session.children_accepted,
            session.stop_children,
        )
        while session.children_accepted < session.stop_children and burst_n < burst_cap:
            target = session.next_target()
            LOG.info("next_target: %s", target)
            if target.get("status") == "complete":
                return session.run_status()

            hints, operator_hint = seat.sampling_hints()
            LOG.info("CoordinatorSeat.sampling_hints: hints=%s operator_hint=%s", hints, operator_hint)

            parent = runner.select_parent(hints, operator_hint)
            LOG.info("select_parent: parent_id=%s operator=%s", parent.get("parent_id"), parent.get("operator"))

            brief = await seat.plan_brief()
            LOG.info("CoordinatorSeat.plan_brief: operator=%s", brief.get("operator"))
            user_prompt = brief.get("prompt", {}).get("user", brief.get("brief", ""))
            if "# Coordination Guidance" in user_prompt:
                LOG.info("HiFo verbal-gradient block present in mutation prompt")
            vlog_block("get_brief / plan_brief (user prompt tail)", user_prompt, max_chars=5000)

            advice = session._advice
            if advice is not None:
                LOG.info(
                    "HiFo advise attribution: regime=%s insights=%s",
                    advice.attribution.get("regime"),
                    advice.attribution.get("insights"),
                )

            while True:
                result = await runner.run_mutation()
                status = result.get("status")
                LOG.info("run_mutation outcome: status=%s keys=%s", status, list(result.keys()))
                if status == "accepted":
                    LOG.info(
                        "accepted child program_id=%s metrics=%s",
                        result.get("program_id"),
                        result.get("metrics"),
                    )
                    burst_n += 1
                    if session._iteration % burst_cap == 0:
                        LOG.info("generation tick at iteration=%d — reflect_generation", session._iteration)
                    break
                if status in ("mutation_failed", "rejected"):
                    LOG.warning("mutation not accepted: %s error=%s", status, result.get("error"))
                    continue
                break

        if session.children_accepted >= session.stop_children:
            break

    if session.children_accepted >= session.stop_children:
        session._phase = "complete"

    final = session.run_status()
    LOG.info("run_agent_mode finished: %s", final)
    return final


async def run_smoke(args: argparse.Namespace) -> dict:
    initial, evaluator, task, _ = load_function_minimization()
    out = args.output_dir
    os.makedirs(out, exist_ok=True)
    cfg = build_config(
        cli=args.cli,
        arm=args.arm,
        stop_children=args.stop_children,
        timeout_s=args.timeout,
        model=args.model,
        output_dir=out,
    )
    ledger = TokenLedger(
        total_budget_tokens=cfg.budget.total_tokens,
        account_caps=cfg.budget.account_caps,
        log_path=os.path.join(out, "llm_calls.jsonl"),
    )
    coordination = build_coordination(args.arm, ledger, cfg.random_seed + 1)
    session = create_agent_session(
        cfg,
        evaluation_file=evaluator,
        initial_program_code=initial,
        output_dir=out,
        task=task,
        coordination=coordination,
    )
    wrap_mutation_backend_verbose(session.mutation_backend, args.timeout)

    print(
        f"burst smoke: cli={args.cli} arm={args.arm} "
        f"stop_children={args.stop_children} timeout={args.timeout}s "
        f"output_dir={out}"
    )
    LOG.info("HiFo immediate params: %s", HIFO_IMMEDIATE_PARAMS)
    LOG.info("substrate steps_per_generation=%d", session.substrate.steps_per_generation)

    status = await run_agent_mode_verbose(session, args.timeout)
    print("run_status:", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging + full coordination prompt blocks",
    )
    parser.add_argument(
        "--cli",
        choices=("claude", "codex", "opencode", "agent"),
        default=os.environ.get("NOEMA_MUTATION_CLI") or "agent",
    )
    parser.add_argument(
        "--arm",
        default="hifo",
        help="coordination.module registry key (default: hifo)",
    )
    parser.add_argument("--example", default="function_minimization")
    parser.add_argument("--stop-children", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--model", default=os.environ.get("NOEMA_MUTATION_MODEL"))
    parser.add_argument(
        "--output-dir",
        default="/tmp/noema-agent-burst-fm-hifo",
    )
    args = parser.parse_args()
    setup_logging(args.verbose)
    if args.example != "function_minimization":
        print(f"only function_minimization is supported, got {args.example!r}", file=sys.stderr)
        return 2
    status = asyncio.run(run_smoke(args))
    return 0 if status.get("stopped") and status.get("children_accepted") == args.stop_children else 1


if __name__ == "__main__":
    raise SystemExit(main())
