"""Construct ``AgentSession`` from ``AgentConfig`` (task 0162)."""

from __future__ import annotations

import os
import random
from typing import Optional

from noema.agenthost.config import AgentConfig
from noema.agenthost.mutation import CliMutationBackend, MutationBackend
from noema.agenthost.session import AgentSession
from noema.budget.ledger import COORDINATION_ACCOUNT, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.coordination import CoordinationModule, build_coordination_module


def _build_coordination(config: AgentConfig, ledger: TokenLedger) -> CoordinationModule:
    coordination_llm = BudgetedLLM(
        model=config.llm.coordination.model,
        ledger=ledger,
        account=COORDINATION_ACCOUNT,
        tag=f"{config.coordination.module}.coordination",
        api_base=config.llm.coordination.api_base,
        api_key=config.llm.coordination.api_key,
        temperature=config.llm.coordination.temperature,
        top_p=config.llm.coordination.top_p,
        max_tokens=config.llm.coordination.max_tokens,
        seed=config.llm.coordination.seed,
        timeout=config.llm.coordination.timeout,
        retries=config.llm.coordination.retries,
        retry_delay=config.llm.coordination.retry_delay,
        total_deadline_s=config.llm.coordination.total_deadline_s,
        disable_reasoning=config.llm.coordination.disable_reasoning,
    )
    params = dict(config.coordination.params)
    params.setdefault("domain_context", config.prompt.system_message)
    return build_coordination_module(
        config.coordination.module,
        params,
        llm=coordination_llm,
        rng=random.Random(config.coordination.seed),
    )


def create_agent_session(
    config: AgentConfig,
    *,
    evaluation_file: str,
    initial_program_code: str,
    output_dir: str,
    mutation_backend: Optional[MutationBackend] = None,
    coordination: Optional[CoordinationModule] = None,
    task: Optional[str] = None,
) -> AgentSession:
    runtime = config.to_runtime_noema()
    ledger = TokenLedger(
        total_budget_tokens=config.budget.total_tokens,
        account_caps=config.budget.account_caps,
        log_path=config.budget.log_path
        or os.path.join(output_dir, "llm_calls.jsonl"),
    )
    if mutation_backend is None:
        mutation_backend = CliMutationBackend(
            kind=config.mutation_cli.kind,
            binary=config.mutation_cli.binary,
            model=config.mutation_cli.model,
            extra_args=list(config.mutation_cli.extra_args),
        )
    if coordination is None:
        coordination = _build_coordination(config, ledger)
    return AgentSession(
        config=runtime,
        evaluation_file=evaluation_file,
        initial_program_code=initial_program_code,
        output_dir=output_dir,
        coordination=coordination,
        ledger=ledger,
        stop_children=config.resolved_stop_children(),
        task=task,
        mutation_backend=mutation_backend,
    )
