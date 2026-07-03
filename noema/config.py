"""
Experiment configuration for noema.

Composes openevolve's component configs (database, evaluator, prompt) with
noema's own budget / coordination / mutation-LLM settings. Defaults deliberately
differ from openevolve where the plan requires it (PLAN.md section 3.4 risk 3):
prompt stochasticity off, evaluator cascade off.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union

import dacite
import yaml

from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig


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


@dataclass
class CoordinationConfig:
    """Which coordination module runs, and its mechanism-specific parameters"""

    module: str = "null"  # registry key: "null" (OFF arm), "hifo", ...
    params: Dict[str, Any] = field(default_factory=dict)
    seed: Optional[int] = None  # module RNG; defaults to NoemaConfig.random_seed + 1


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
    llm: LLMClientConfig = field(default_factory=LLMClientConfig)
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)

    def __post_init__(self):
        if self.prompt.use_template_stochasticity:
            raise ValueError(
                "noema requires prompt.use_template_stochasticity=False "
                "(identical prompts across arms)"
            )
        if self.coordination.seed is None:
            self.coordination.seed = self.random_seed + 1

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "NoemaConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NoemaConfig":
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
