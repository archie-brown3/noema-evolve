"""Construct ``AgentSession`` from ``AgentConfig`` (task 0162)."""

from __future__ import annotations

import os
import random
from typing import Callable, Optional

from noema.agenthost.config import AgentConfig, warn_agent_config_differences
from noema.agenthost.mutation import CliMutationBackend, MutationBackend
from noema.agenthost.reasoning import DeepCoordinationLLM
from noema.agenthost.session import AgentSession
from noema.budget.cli_runner import CliPtyRunner
from noema.budget.ledger import COORDINATION_ACCOUNT, TokenLedger
from noema.budget.llm import build_budgeted_llm
from noema.coordination import CoordinationModule, build_coordination_module


def _build_coordination_seat(
    config: AgentConfig,
    ledger: TokenLedger,
    output_dir: str,
    *,
    tag: str,
    model: Optional[str] = None,
    cli_runner: Optional[CliPtyRunner] = None,
    on_session_start: Optional[Callable[[str], None]] = None,
):
    noema = config.noema
    seat = build_budgeted_llm(
        noema.llm.coordination,
        ledger=ledger,
        account=COORDINATION_ACCOUNT,
        tag=tag,
        model=model,
    )
    if config.coordination_depth == "deep":
        return DeepCoordinationLLM(
            seat,
            cli=config.coordination_cli,
            output_dir=output_dir,
            runner=cli_runner,
            on_session_start=on_session_start,
        )
    return seat


def _build_coordination(
    config: AgentConfig,
    ledger: TokenLedger,
    output_dir: str,
    *,
    cli_runner: Optional[CliPtyRunner] = None,
    on_session_start: Optional[Callable[[str], None]] = None,
) -> CoordinationModule:
    noema = config.noema
    coordination_llm = _build_coordination_seat(
        config,
        ledger,
        output_dir,
        tag=f"{noema.coordination.module}.coordination",
        cli_runner=cli_runner,
        on_session_start=on_session_start,
    )
    params = dict(noema.coordination.params)
    params.setdefault("domain_context", noema.prompt.system_message)
    params.setdefault("escalation_model", noema.llm.coordination.model)
    return build_coordination_module(
        noema.coordination.module,
        params,
        llm=coordination_llm,
        rng=random.Random(noema.coordination.seed),
    )


def _wire_alternate_tiers(
    coordination: CoordinationModule,
    config: AgentConfig,
    ledger: TokenLedger,
    output_dir: str,
    *,
    cli_runner: Optional[CliPtyRunner] = None,
    on_session_start: Optional[Callable[[str], None]] = None,
) -> None:
    params = config.noema.coordination.params
    _wire_alternate_tier(
        coordination,
        "set_paradigm_llm",
        params.get("paradigm_model"),
        config,
        ledger,
        output_dir,
        tag=f"{config.noema.coordination.module}.paradigm",
        cli_runner=cli_runner,
        on_session_start=on_session_start,
    )
    _wire_alternate_tier(
        coordination,
        "set_variant_llm",
        params.get("variant_model"),
        config,
        ledger,
        output_dir,
        tag=f"{config.noema.coordination.module}.variant",
        cli_runner=cli_runner,
        on_session_start=on_session_start,
    )


def _wire_alternate_tier(
    coordination: CoordinationModule,
    setter_name: str,
    model_name: Optional[str],
    config: AgentConfig,
    ledger: TokenLedger,
    output_dir: str,
    *,
    tag: str,
    cli_runner: Optional[CliPtyRunner] = None,
    on_session_start: Optional[Callable[[str], None]] = None,
) -> None:
    noema = config.noema
    if not model_name or model_name == noema.llm.coordination.model:
        return
    setter = getattr(coordination, setter_name, None)
    if setter is None:
        return
    setter(
        _build_coordination_seat(
            config,
            ledger,
            output_dir,
            tag=tag,
            model=model_name,
            cli_runner=cli_runner,
            on_session_start=on_session_start,
        )
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
    mutation_runner: Optional[CliPtyRunner] = None,
    coordination_runner: Optional[CliPtyRunner] = None,
    on_mutation_session_start: Optional[Callable[[str], None]] = None,
    on_coordination_session_start: Optional[Callable[[str], None]] = None,
    attempt_trace_callback: Optional[Callable[[dict], None]] = None,
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
            runner=mutation_runner,
            on_session_start=on_mutation_session_start,
        )
    warn_agent_config_differences(
        config,
        mutation_is_cli=isinstance(mutation_backend, CliMutationBackend),
    )
    built_coordination = coordination is None
    if built_coordination:
        coordination = _build_coordination(
            config,
            ledger,
            output_dir,
            cli_runner=coordination_runner,
            on_session_start=on_coordination_session_start,
        )
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
        attempt_trace_callback=attempt_trace_callback,
    )
    if built_coordination:
        _wire_alternate_tiers(
            session.coordination,
            config,
            ledger,
            output_dir,
            cli_runner=coordination_runner,
            on_session_start=on_coordination_session_start,
        )
    _bind_deep_coordination(session.coordination, session)
    if config.mutation_depth == "deep" and isinstance(session.mutation_backend, CliMutationBackend):
        session.mutation_backend.bind_session(session)
    return session


def _bind_deep_coordination(coordination: CoordinationModule, session: AgentSession) -> None:
    seen = set()
    for llm in (
        getattr(coordination, "llm", None),
        getattr(coordination, "_paradigm_llm", None),
        getattr(coordination, "_variant_llm", None),
    ):
        if id(llm) in seen:
            continue
        seen.add(id(llm))
        if isinstance(llm, DeepCoordinationLLM):
            llm.bind_session(session)
