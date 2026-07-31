"""Agent-host configuration (side research, task 0160).

Architecture
------------
``AgentSession`` (the orchestrator — analogous to ``NoemaController``) is the
**only** layer that owns config. Nested mutation is deliberately **stateless**:
``MutationBackend.run(MutationRequest)`` receives a fully assembled prompt,
parent code, and host-owned layout paths. It does not receive ``AgentConfig``.
That keeps CLI transport swappable (claude / codex / opencode / Fake) without
re-threading experiment knobs through every backend.

Bootstrap
---------
Start from ``noema.config.NoemaConfig`` — the same YAML shape dissertation runs
already use — then **drop or replace** fields that assume an in-process
mutation ``BudgetedLLM``. Reuse shared nested types where the scientific seams
are identical (``database``, ``evaluator``, ``prompt``, ``coordination``
without escalation, ``substrate``, ``selection``, budget for coordination).

Ownership map (who reads which knobs)
-------------------------------------
::

    YAML / AgentConfig
            │
            ▼
      AgentSession.__init__          ← sole config consumer
            │
            ├── build_substrate_runtime(config)   # substrate + selection + database
            ├── build_coordination_module(...)    # coordination.module (+ llm.coordination)
            ├── make_prompt_sampler(config.prompt)
            ├── make_evaluator(config.evaluator)
            ├── TokenLedger(config.budget)        # coordination tokens only
            ├── choose_operator(...)              # mutation_operators / diff_based_*
            └── CliMutationBackend(kind=...)      # from mutation_cli — constructed
                                                  # BY the session factory, not by
                                                  # the backend reading config

    MutationBackend                   ← no config; MutationRequest only

See Vault: [[NoemaAgent Host — AgentConfig]].
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    LLMClientConfig,
    LLMRolesConfig,
    NoemaConfig,
    SelectionConfig,
    SubstrateConfig,
    _default_evaluator_config,
    _default_prompt_config,
)
from noema.agenthost.cli_backends import SUPPORTED_MUTATION_CLIS


# ---------------------------------------------------------------------------
# Nested: mutation transport (REPLACES NoemaConfig.llm.mutation)
# ---------------------------------------------------------------------------

@dataclass
class MutationCliConfig:
    """How the orchestrator spawns the nested coding CLI.

    Replaces ``NoemaConfig.llm.mutation`` (``BudgetedLLM`` seat). The CLI owns
    model choice and mutation tokens; Noema does not meter them on the ledger.

    Kept out of ``MutationBackend`` itself: the session factory builds
    ``CliMutationBackend(kind=..., model=..., ...)`` from this block and injects
    the backend. Per-call timeout may override ``timeout_s`` on ``run_mutation``.
    """

    kind: str = "opencode"  # claude | codex | opencode
    binary: Optional[str] = None  # None → resolve on PATH
    model: Optional[str] = None  # CLI-specific model flag when set
    extra_args: List[str] = field(default_factory=list)
    timeout_s: float = 600.0

    # Intentionally NOT here (dropped from llm.mutation):
    #   api_base, api_key, temperature, top_p, max_tokens, seed,
    #   retries, retry_delay, total_deadline_s, disable_reasoning
    # Those configure OpenAI-compatible HTTP clients. Headless CLIs have their
    # own auth/config files (~/.claude, ~/.codex, opencode auth).


# ---------------------------------------------------------------------------
# Nested: coordination LLM only (DROPS mutation seat + escalation defaults)
# ---------------------------------------------------------------------------

@dataclass
class AgentLLMConfig:
    """LLM seats under the agent host.

    KEEP: ``coordination`` — HiFo / PES / PE still call ``BudgetedLLM`` through
    the coordination module; that seat stays on the Noema ledger.

    DROP: ``mutation`` — there is no mutation ``BudgetedLLM``. Nested CLI is
    ``MutationCliConfig`` above.
    """

    coordination: LLMClientConfig = field(default_factory=LLMClientConfig)


@dataclass
class AgentCoordinationConfig:
    """Coordination module selection for the agent host.

    KEEP from ``CoordinationConfig``: ``module``, ``params``, ``seed``.

    DROP / force inert: ``escalation`` — host-side model escalation steers
    ``Advice.model`` into ``BudgetedLLM.generate_with_context``. Under CLI
    mutation that path does not exist (Derive note E7). If a YAML still carries
    ``escalation``, loaders should ignore or reject it rather than pretend.
    """

    module: str = "null"
    params: Dict[str, Any] = field(default_factory=dict)
    seed: Optional[int] = None
    # escalation: deliberately omitted


# ---------------------------------------------------------------------------
# Master agent-host config
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Master configuration for an ``AgentSession`` run.

    Bootstrap rule: same scientific knobs as ``NoemaConfig`` where the
    orchestrator still drives the same seams; replace only the mutation
    transport; drop controller-only mutation metering / escalation.
    """

    # ----- Loop / stop (KEEP, with agent-facing alias) ---------------------
    # Controller: max_iterations bounds the for-loop.
    # Agent host: stop_children is the primary stop (accepted children).
    # Keep max_iterations as a YAML-compatible alias that defaults stop_children
    # when stop_children is None (mirrors today's AgentSession constructor).
    max_iterations: int = 100
    stop_children: Optional[int] = None  # None → use max_iterations
    checkpoint_interval: int = 50  # KEEP for future checkpoint tool; unused in v1 path
    random_seed: int = 42

    # ----- Program representation (KEEP — prompt assemble + materialize) ---
    language: str = "python"
    file_suffix: str = ".py"
    diff_based_evolution: bool = True
    diff_pattern: str = r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE"
    max_code_length: int = 10000

    # ----- Retry (KEEP — orchestrator owns retry_cap semantics) ------------
    # Backend failures stay at phase briefed without consuming eval retry_cap;
    # eval rejections use the same advice/retry composition as the controller.
    retry_enabled: bool = False
    retry_cap: int = 2
    retry_on: str = "failure"  # "non_improvement" may stay deferred under agent host

    # ----- Operator menu (KEEP — choose_operator on select_parent) ---------
    mutation_operators: Optional[List[str]] = None
    mutation_operator_seed: Optional[int] = None

    # ----- Prompt context sizes (KEEP — build_mutation_prompt) -------------
    num_inspirations: int = 3
    num_top_programs: int = 5
    num_previous_programs: int = 3
    prompt_metric_fields: Optional[Set[str]] = None

    # ----- Shared OpenEvolve / Noema nested configs (KEEP) -----------------
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    evaluator: EvaluatorConfig = field(default_factory=_default_evaluator_config)
    prompt: PromptConfig = field(default_factory=_default_prompt_config)
    # Budget meters coordination (+ future host) calls only — NOT nested CLI.
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    substrate: SubstrateConfig = field(default_factory=SubstrateConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)

    # ----- Agent-host replacements ----------------------------------------
    coordination: AgentCoordinationConfig = field(default_factory=AgentCoordinationConfig)
    llm: AgentLLMConfig = field(default_factory=AgentLLMConfig)
    mutation_cli: MutationCliConfig = field(default_factory=MutationCliConfig)

    def __post_init__(self) -> None:
        validate_agent_config(self)
        if self.coordination.seed is None:
            self.coordination.seed = self.random_seed + 1
        if self.selection.seed is None:
            self.selection.seed = self.random_seed + 3
        if self.mutation_operator_seed is None:
            self.mutation_operator_seed = self.random_seed + 2
        if self.substrate.cvt_seed is None:
            self.substrate.cvt_seed = self.random_seed + 4

    def resolved_stop_children(self) -> int:
        return self.stop_children if self.stop_children is not None else self.max_iterations

    def to_runtime_noema(self) -> NoemaConfig:
        """Runtime ``NoemaConfig`` for substrate / evaluator / session seams."""
        return NoemaConfig(
            max_iterations=self.resolved_stop_children(),
            checkpoint_interval=self.checkpoint_interval,
            random_seed=self.random_seed,
            language=self.language,
            file_suffix=self.file_suffix,
            diff_based_evolution=self.diff_based_evolution,
            diff_pattern=self.diff_pattern,
            max_code_length=self.max_code_length,
            retry_enabled=self.retry_enabled,
            retry_cap=self.retry_cap,
            retry_on=self.retry_on,
            mutation_operators=(
                list(self.mutation_operators)
                if self.mutation_operators is not None
                else None
            ),
            mutation_operator_seed=self.mutation_operator_seed,
            num_inspirations=self.num_inspirations,
            num_top_programs=self.num_top_programs,
            num_previous_programs=self.num_previous_programs,
            prompt_metric_fields=(
                set(self.prompt_metric_fields)
                if self.prompt_metric_fields is not None
                else None
            ),
            database=copy.deepcopy(self.database),
            evaluator=copy.deepcopy(self.evaluator),
            prompt=copy.deepcopy(self.prompt),
            budget=copy.deepcopy(self.budget),
            substrate=copy.deepcopy(self.substrate),
            selection=copy.deepcopy(self.selection),
            llm=LLMRolesConfig(coordination=copy.deepcopy(self.llm.coordination)),
            coordination=CoordinationConfig(
                module=self.coordination.module,
                params=dict(self.coordination.params),
                seed=self.coordination.seed,
                escalation=None,
            ),
        )


def agent_config_from_noema(
    noema_cfg: NoemaConfig,
    **mutation_cli_overrides: Any,
) -> AgentConfig:
    """Project ``NoemaConfig`` into ``AgentConfig`` (drops mutation LLM + escalation)."""
    mutation_cli = MutationCliConfig(**mutation_cli_overrides)
    return AgentConfig(
        max_iterations=noema_cfg.max_iterations,
        stop_children=noema_cfg.max_iterations,
        checkpoint_interval=noema_cfg.checkpoint_interval,
        random_seed=noema_cfg.random_seed,
        language=noema_cfg.language,
        file_suffix=noema_cfg.file_suffix,
        diff_based_evolution=noema_cfg.diff_based_evolution,
        diff_pattern=noema_cfg.diff_pattern,
        max_code_length=noema_cfg.max_code_length,
        retry_enabled=noema_cfg.retry_enabled,
        retry_cap=noema_cfg.retry_cap,
        retry_on=noema_cfg.retry_on,
        mutation_operators=(
            list(noema_cfg.mutation_operators)
            if noema_cfg.mutation_operators is not None
            else None
        ),
        mutation_operator_seed=noema_cfg.mutation_operator_seed,
        num_inspirations=noema_cfg.num_inspirations,
        num_top_programs=noema_cfg.num_top_programs,
        num_previous_programs=noema_cfg.num_previous_programs,
        prompt_metric_fields=(
            set(noema_cfg.prompt_metric_fields)
            if noema_cfg.prompt_metric_fields is not None
            else None
        ),
        database=copy.deepcopy(noema_cfg.database),
        evaluator=copy.deepcopy(noema_cfg.evaluator),
        prompt=copy.deepcopy(noema_cfg.prompt),
        budget=copy.deepcopy(noema_cfg.budget),
        substrate=copy.deepcopy(noema_cfg.substrate),
        selection=copy.deepcopy(noema_cfg.selection),
        coordination=AgentCoordinationConfig(
            module=noema_cfg.coordination.module,
            params=dict(noema_cfg.coordination.params),
            seed=noema_cfg.coordination.seed,
        ),
        llm=AgentLLMConfig(coordination=copy.deepcopy(noema_cfg.llm.coordination)),
        mutation_cli=mutation_cli,
    )


def validate_agent_config(config: AgentConfig) -> None:
    """Validate agent-host invariants."""
    if config.retry_on not in ("failure", "non_improvement"):
        raise ValueError(
            f'retry_on must be "failure" or "non_improvement", got {config.retry_on!r}'
        )
    if config.mutation_cli.kind not in SUPPORTED_MUTATION_CLIS:
        raise ValueError(
            f"mutation_cli.kind must be one of {SUPPORTED_MUTATION_CLIS}, "
            f"got {config.mutation_cli.kind!r}"
        )
    if config.prompt.use_template_stochasticity:
        raise ValueError(
            "agent host requires prompt.use_template_stochasticity=False "
            "(identical shared prompt prefix across arms)"
        )
