"""
Prompt assembly for noema.

All arms of an experiment share identical prompts except for the coordination
block. Two rules enforce that here:

1. Template stochasticity is forced OFF — openevolve defaults it ON, which
   randomizes prompt phrasing and voids identical-prompt guarantees.
2. Coordination advice is injected only as a clearly-delimited suffix appended
   AFTER openevolve's PromptSampler has built the prompt, so the shared prefix
   is byte-identical across arms and the injected block is the single
   controlled variable (trivially verifiable by diffing logged prompts).
"""

from typing import Any, Dict, List, Optional

from openevolve.config import PromptConfig
from openevolve.database import Program
from openevolve.prompt.sampler import PromptSampler

from noema.evolution.operators import OPERATOR_TEMPLATES

# Delimits the coordination block in the user prompt. The header is part of the
# controlled variable: the coordination-OFF arm has no block and no header.
COORDINATION_HEADER = "\n\n# Coordination Guidance\n"
SYSTEM_SUFFIX_SEPARATOR = "\n\n"


def make_prompt_sampler(config: Optional[PromptConfig] = None) -> PromptSampler:
    """Build openevolve's PromptSampler with stochasticity forced off"""
    if config is None:
        config = PromptConfig()
    if config.use_template_stochasticity:
        raise ValueError(
            "noema requires prompt.use_template_stochasticity=False; "
            "random phrase variations void the identical-prompts guarantee across arms"
        )
    sampler = PromptSampler(config)
    # NOEMA: register the EoH-derived operator menu's templates (task 0027).
    # Registering unconditionally is harmless when mutation_operators is None
    # (legacy path) — unused registered templates change nothing.
    for template_key, template_text in OPERATOR_TEMPLATES.items():
        sampler.template_manager.add_template(template_key, template_text)
    return sampler


def _filter_metrics(metrics: Dict[str, Any], fields: Optional[set]) -> Dict[str, Any]:
    """Keep only the named metric fields; None means keep all."""
    if fields is None:
        return metrics
    return {k: v for k, v in metrics.items() if k in fields}


def _filter_program_list(programs: List[Dict], fields: Optional[set]) -> List[Dict]:
    if fields is None:
        return programs
    result = []
    for p in programs:
        p = dict(p)
        if "metrics" in p:
            p["metrics"] = _filter_metrics(p["metrics"], fields)
        result.append(p)
    return result


def build_mutation_prompt(
    sampler: PromptSampler,
    parent: Program,
    top_programs: List[Program],
    previous_programs: List[Program],
    inspirations: List[Program],
    language: str,
    iteration: int,
    diff_based_evolution: bool,
    feature_dimensions: List[str],
    parent_artifacts: Optional[Dict[str, Any]] = None,
    template_key: Optional[str] = None,
    parent2: Optional[Program] = None,
    metric_fields: Optional[set] = None,
) -> Dict[str, str]:
    """Assemble the shared (pre-coordination) mutation prompt via openevolve"""
    return sampler.build_prompt(
        current_program=parent.code,
        parent_program=parent.code,
        program_metrics=_filter_metrics(parent.metrics, metric_fields),
        previous_programs=_filter_program_list([p.to_dict() for p in previous_programs], metric_fields),
        top_programs=_filter_program_list([p.to_dict() for p in top_programs], metric_fields),
        inspirations=_filter_program_list([p.to_dict() for p in inspirations], metric_fields),
        language=language,
        evolution_round=iteration,
        diff_based_evolution=diff_based_evolution,
        program_artifacts=parent_artifacts if parent_artifacts else None,
        feature_dimensions=feature_dimensions,
        template_key=template_key,
        parent2_program=parent2.code if parent2 else "",
    )


def inject_advice(prompt: Dict[str, str], prompt_block: str, system_block: str) -> Dict[str, str]:
    """
    Append coordination text to an assembled prompt.

    Empty blocks return the prompt byte-identical (the coordination-OFF arm) —
    tests assert this property.
    """
    system = prompt["system"]
    user = prompt["user"]
    if system_block:
        system = system + SYSTEM_SUFFIX_SEPARATOR + system_block
    if prompt_block:
        user = user + COORDINATION_HEADER + prompt_block
    return {"system": system, "user": user}
