"""
Experiment configuration for noema.

Composes openevolve's component configs (database, evaluator, prompt) with
noema's own budget / coordination / mutation-LLM settings. Defaults deliberately
differ from openevolve where the plan requires it (PLAN.md section 3.4 risk 3):
prompt stochasticity off, evaluator cascade off.
"""

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import dacite
import yaml

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig
from noema.operators import OPERATOR_MENU


def _default_prompt_config() -> PromptConfig:
    # openevolve defaults stochasticity ON; noema requires it OFF (identical
    # prompts across arms)
    return PromptConfig(use_template_stochasticity=False)


def _default_evaluator_config() -> EvaluatorConfig:
    # openevolve defaults cascade ON, which warns and falls back unless the
    # eval script defines evaluate_stage1
    return EvaluatorConfig(cascade_evaluation=False)


@dataclass
class BudgetConfig:
    """Token budget: one shared pool, per-account accounting (PLAN.md 3.2)"""

    total_tokens: int = 1_000_000
    account_caps: Dict[str, int] = field(default_factory=dict)
    log_path: Optional[str] = None  # JSONL of every CallRecord; defaults under output_dir


@dataclass
class LLMClientConfig:
    """Settings for a BudgetedLLM client"""

    model: str = "gpt-4o-mini"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = None
    max_tokens: Optional[int] = 4096
    seed: Optional[int] = None
    timeout: float = 60.0
    retries: int = 3
    retry_delay: float = 5.0
    # task 0103: httpx's `timeout` is a per-chunk read timeout that resets on
    # every streamed token, so a degenerate slow-reasoning dribble never trips
    # it — a live run hung 24+ minutes on one call before this existed. This
    # bounds generate_with_context's WHOLE retry loop (all attempts combined),
    # not any single request. 600s default: legitimate calls observed up to 160s.
    total_deadline_s: float = 600.0


@dataclass
class LLMRolesConfig:
    """Per-seat model selection.

    The two seats are budgeted separately already (MUTATION_ACCOUNT /
    COORDINATION_ACCOUNT); this lets them differ in model too, so a run can
    spend its fixed token pool asymmetrically — cheap high-volume mutation,
    expensive low-volume coordination. Both roles default to the same settings,
    so a single-model config behaves exactly as it did before the split.
    """

    mutation: LLMClientConfig = field(default_factory=LLMClientConfig)
    coordination: LLMClientConfig = field(default_factory=LLMClientConfig)


@dataclass
class CoordinationConfig:
    """Which coordination module runs, and its mechanism-specific parameters"""

    module: str = "null"  # registry key: "null" (OFF arm), "hifo", ...
    params: Dict[str, Any] = field(default_factory=dict)
    seed: Optional[int] = None  # module RNG; defaults to NoemaConfig.random_seed + 1


@dataclass
class SubstrateConfig:
    """Population topology/storage implementation."""

    kind: str = "islands"
    steps_per_generation: Optional[int] = None


@dataclass
class SelectionConfig:
    """Parent-selection policy, configured independently of population topology."""

    policy: str = "substrate_default"
    seed: Optional[int] = None
    boltzmann_temperature: float = 1.0
    boltzmann_exploration_rate: float = 0.2
    stagnation_detection_enabled: bool = False
    stagnation_mode: str = "released"
    initial_exploration: float = 0.1
    widening_alpha: float = 0.5


@dataclass
class NoemaConfig:
    """Master configuration for a noema run"""

    # Loop settings
    max_iterations: int = 100
    checkpoint_interval: int = 50
    random_seed: int = 42

    # Program representation
    language: str = "python"
    file_suffix: str = ".py"
    diff_based_evolution: bool = True
    diff_pattern: str = r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE"
    max_code_length: int = 10000

    # Retry loop (substrate-level, identical across arms).
    # retry_cap counts RETRIES after the initial attempt (LoongFlow's
    # max_rounds ~= retry_cap + 1 total rounds). retry_on picks the trigger
    # (task 0062): "failure" = parse/boundary/eval failures only (today's
    # behavior); "non_improvement" additionally retries a valid child whose
    # fitness does not beat its parent, keeping the best attempt
    # (execute_agent_chat.py round semantics). Inert unless retry_enabled.
    retry_enabled: bool = False
    retry_cap: int = 2
    retry_on: str = "failure"

    # EoH-derived mutation operator menu (substrate-level, task 0027).
    # None = legacy path, zero behavior change (today's diff_based_evolution
    # toggle is the sole control). Strictly opt-in.
    mutation_operators: Optional[List[str]] = None
    mutation_operator_seed: Optional[int] = None  # defaults to random_seed + 2

    # Prompt context sizes (mirrors openevolve's iteration defaults)
    num_inspirations: int = 3
    num_top_programs: int = 5
    num_previous_programs: int = 3

    # Borrowed component configs
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    evaluator: EvaluatorConfig = field(default_factory=_default_evaluator_config)
    prompt: PromptConfig = field(default_factory=_default_prompt_config)

    # noema-owned configs
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    llm: LLMRolesConfig = field(default_factory=LLMRolesConfig)
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)
    substrate: SubstrateConfig = field(default_factory=SubstrateConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)

    def __post_init__(self):
        if self.retry_on not in ("failure", "non_improvement"):
            raise ValueError(
                f'retry_on must be "failure" or "non_improvement", got {self.retry_on!r}'
            )
        if self.prompt.use_template_stochasticity:
            raise ValueError(
                "noema requires prompt.use_template_stochasticity=False "
                "(identical prompts across arms)"
            )
        if self.coordination.seed is None:
            self.coordination.seed = self.random_seed + 1
        if self.selection.seed is None:
            self.selection.seed = self.random_seed + 3
        if self.substrate.kind not in ("islands", "tree"):
            raise ValueError(f"unknown substrate kind {self.substrate.kind!r}")
        if (
            self.substrate.steps_per_generation is not None
            and self.substrate.steps_per_generation <= 0
        ):
            raise ValueError("substrate.steps_per_generation must be positive")
        if self.selection.policy not in (
            "substrate_default",
            "stock_openevolve",
            "boltzmann",
            "uct",
        ):
            raise ValueError(f"unknown selection policy {self.selection.policy!r}")
        if self.selection.boltzmann_temperature <= 0:
            raise ValueError("selection.boltzmann_temperature must be positive")
        if not 0 <= self.selection.boltzmann_exploration_rate <= 1:
            raise ValueError(
                "selection.boltzmann_exploration_rate must be between 0 and 1"
            )
        if (
            not math.isfinite(self.selection.initial_exploration)
            or self.selection.initial_exploration < 0
        ):
            raise ValueError(
                "selection.initial_exploration must be finite and non-negative"
            )
        if (
            not math.isfinite(self.selection.widening_alpha)
            or not 0 < self.selection.widening_alpha <= 1
        ):
            raise ValueError(
                "selection.widening_alpha must be finite and in (0, 1]"
            )
        if self.mutation_operator_seed is None:
            self.mutation_operator_seed = self.random_seed + 2
        if self.mutation_operators is not None:
            unknown = [n for n in self.mutation_operators if n not in OPERATOR_MENU]
            if unknown:
                raise ValueError(
                    f"unknown mutation operator(s) {unknown}; valid names are "
                    f"{sorted(OPERATOR_MENU)}"
                )
            if self.prompt.programs_as_changes_description and any(
                OPERATOR_MENU[n].parse_mode == "full_rewrite" for n in self.mutation_operators
            ):
                raise ValueError(
                    "prompt.programs_as_changes_description=True requires every "
                    "selected mutation operator to be parse_mode='diff' (mirrors "
                    "openevolve's own diff_based_evolution validator)"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Fully-resolved config (CLI-derived values and nested dataclasses included)"""
        return asdict(self)

    def to_yaml(self) -> str:
        """Deterministic YAML text for freezing/hashing a run's config"""
        return yaml.safe_dump(self.to_dict(), sort_keys=True)

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "NoemaConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    # noema-owned config sections whose keys are validated for typos (task 0056).
    # The borrowed openevolve sections (database/evaluator/prompt) are left lenient
    # — matching openevolve's own non-strict from_dict, and because their key set
    # is openevolve's contract, not noema's to police.
    _VALIDATED_SECTIONS = {
        "budget": BudgetConfig,
        "llm": LLMRolesConfig,
        "coordination": CoordinationConfig,
        "substrate": SubstrateConfig,
        "selection": SelectionConfig,
    }

    @classmethod
    def _reject_unknown_keys(cls, data: Dict[str, Any]) -> None:
        """Fail loud on a misspelled config key (task 0056 item 1).

        dacite runs non-strict (so borrowed openevolve sections may carry extra
        keys), which means a typo like `diff_based_evoluton:` or `coordination:
        {modul: hifo}` is otherwise SILENTLY dropped and the default used —
        quietly reverting an arm's setting. In a study where arms differ in
        exactly one config field, that can invalidate a comparison. Validate the
        top level and noema's own sections before the freeze; leave the borrowed
        openevolve sections lenient.
        """
        import dataclasses

        known_top = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known_top
        if unknown:
            raise ValueError(
                f"unknown top-level config key(s): {sorted(unknown)}. "
                f"Known keys: {sorted(known_top)}"
            )
        for name, section_cls in cls._VALIDATED_SECTIONS.items():
            section = data.get(name)
            if isinstance(section, dict):
                known = {f.name for f in dataclasses.fields(section_cls)}
                bad = set(section) - known
                if bad:
                    raise ValueError(
                        f"unknown key(s) in config section '{name}': {sorted(bad)}. "
                        f"Known keys: {sorted(known)}"
                    )
        # `llm` nests a whole LLMClientConfig per role, so the loop above only
        # checks the role names. Typos inside a role would otherwise slip past
        # the very check this method exists for. _normalise_llm_section has
        # already run, so both roles are present as dicts.
        llm = data.get("llm")
        if isinstance(llm, dict):
            known = {f.name for f in dataclasses.fields(LLMClientConfig)}
            for role in ("mutation", "coordination"):
                bad = set(llm.get(role) or {}) - known
                if bad:
                    raise ValueError(
                        f"unknown key(s) in config section 'llm.{role}': "
                        f"{sorted(bad)}. Known keys: {sorted(known)}"
                    )

    @staticmethod
    def _normalise_llm_section(data: Dict[str, Any]) -> Dict[str, Any]:
        """Accept both LLM config shapes, lifting the flat one to per-role.

        Flat (`llm: {model: X, ...}`) gives both roles X — the pre-split
        behaviour, kept working indefinitely so existing configs and the frozen
        run configs under runs/ stay loadable for checkpoint resume.
        Per-role (`llm: {mutation: {...}, coordination: {...}}`) passes through.
        """
        llm = data.get("llm")
        if not isinstance(llm, dict):
            return data
        roles = {"mutation", "coordination"}
        named = roles & llm.keys()
        if not named:
            # Two independent copies: dacite must build two LLMClientConfigs,
            # not one object aliased into both seats.
            return {**data, "llm": {"mutation": llm, "coordination": dict(llm)}}
        if len(named) == 1:
            missing = (roles - named).pop()
            raise ValueError(
                f"config section 'llm' names {named.pop()!r} but not {missing!r}; "
                f"a per-role llm config must set both. Otherwise {missing!r} "
                f"silently falls back to the default model, which in a study "
                f"where arms differ in one setting invalidates the comparison."
            )
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NoemaConfig":
        data = cls._normalise_llm_section(data)
        cls._reject_unknown_keys(data)
        return dacite.from_dict(
            data_class=cls,
            data=data,
            config=dacite.Config(
                cast=[list, dict],
                strict=False,
                # DatabaseConfig.novelty_llm is annotated with a forward ref
                # (same workaround as openevolve.config.Config.from_dict)
                forward_references={"LLMInterface": Any},
            ),
        )
