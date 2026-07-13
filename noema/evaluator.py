"""
Adapter around openevolve.evaluator.Evaluator.

noema constructs the evaluator with no LLM ensemble, no prompt sampler and no
database, so the use_llm_feedback path (an unmetered LLM call site,
PLAN.md section 1.3 site #3) is structurally dead, not just configured off.
"""

from typing import Optional

from openevolve.config import EvaluatorConfig
from openevolve.evaluator import Evaluator


def make_evaluator(
    config: Optional[EvaluatorConfig] = None,
    evaluation_file: str = "",
    suffix: str = ".py",
) -> Evaluator:
    """
    Build an OpenEvolve Evaluator for library use.

    Note the defaults deliberately differ from openevolve's:
    - cascade_evaluation=False (openevolve defaults True, which warns and falls
      back unless the eval script defines evaluate_stage1);
    - use_llm_feedback is rejected outright — that call site would bypass the
      token ledger.
    """
    if config is None:
        config = EvaluatorConfig(cascade_evaluation=False)
    if config.use_llm_feedback:
        raise ValueError(
            "noema requires evaluator.use_llm_feedback=False; "
            "its LLM calls would bypass the token ledger"
        )
    return Evaluator(
        config,
        evaluation_file,
        llm_ensemble=None,
        prompt_sampler=None,
        database=None,
        suffix=suffix,
    )
