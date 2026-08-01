"""Construct ``AgentSession`` from ``AgentConfig`` (task 0162)."""

from __future__ import annotations

import os
import random
from typing import Optional

from noema.agenthost.config import AgentConfig, warn_agent_config_differences
from noema.agenthost.mutation import CliMutationBackend, MutationBackend
from noema.agenthost.reasoning import DeepCoordinationLLM
from noema.agenthost.session import AgentSession
from noema.budget.ledger import COORDINATION_ACCOUNT, TokenLedger
from noema.budget.llm import BudgetedLLM, build_budgeted_llm
from noema.coordination import CoordinationModule, build_coordination_module


def _build_coordination(
    config: AgentConfig,
    ledger: TokenLedger,
    output_dir: str,
) -> CoordinationModule:
    noema = config.noema
    coordination_llm: BudgetedLLM | DeepCoordinationLLM = build_budgeted_llm(
        noema.llm.coordination,
        ledger=ledger,
        account=COORDINATION_ACCOUNT,
        tag=f"{noema.coordination.module}.coordination",
    )
    if config.coordination_depth == "deep":
        coordination_llm = DeepCoordinationLLM(
            coordination_llm,
            cli=config.coordination_cli,
            output_dir=output_dir,
        )
    params = dict(noema.coordination.params)
    params.setdefault("domain_context", noema.prompt.system_message)
    return build_coordination_module(
        noema.coordination.module,
        params,
        llm=coordination_llm,
        rng=random.Random(noema.coordination.seed),
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
    noema = config.noema
    ledger = TokenLedger(
        total_budget_tokens=noema.budget.total_tokens,
        account_caps=noema.budget.account_caps,
        log_path=noema.budget.log_path or os.path.join(output_dir, "llm_calls.jsonl"),
    )
    if mutation_backend is None:
        mutation_backend = CliMutationBackend(
            kind=config.mutation_cli.kind,
            binary=config.mutation_cli.binary,
            model=config.mutation_cli.model,
            extra_args=list(config.mutation_cli.extra_args),
        )
    warn_agent_config_differences(
        config,
        mutation_is_cli=isinstance(mutation_backend, CliMutationBackend),
    )
    if coordination is None:
        coordination = _build_coordination(config, ledger, output_dir)
    session = AgentSession(
        config=noema,
        evaluation_file=evaluation_file,
        initial_program_code=initial_program_code,
        output_dir=output_dir,
        coordination=coordination,
        ledger=ledger,
        stop_children=config.resolved_stop_children(),
        task=task,
        mutation_backend=mutation_backend,
        mutation_timeout_s=config.mutation_cli.timeout_s,
    )
    if isinstance(session.coordination.llm, DeepCoordinationLLM):
        session.coordination.llm.bind_session(session)
    if config.mutation_depth == "deep" and isinstance(session.mutation_backend, CliMutationBackend):
        session.mutation_backend.bind_session(session)
    return session
