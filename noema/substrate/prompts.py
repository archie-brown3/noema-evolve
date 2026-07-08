"""
Prompt assembly for noema.

All arms of an experiment share identical prompts except for the coordination
block (PLAN.md sections 1.5 and 3.4 risk 3). Two rules enforce that here:

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
    return PromptSampler(config)


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
) -> Dict[str, str]:
    """Assemble the shared (pre-coordination) mutation prompt via openevolve"""
    return sampler.build_prompt(
        current_program=parent.code,
        parent_program=parent.code,
        program_metrics=parent.metrics,
        previous_programs=[p.to_dict() for p in previous_programs],
        top_programs=[p.to_dict() for p in top_programs],
        inspirations=[p.to_dict() for p in inspirations],
        language=language,
        evolution_round=iteration,
        diff_based_evolution=diff_based_evolution,
        program_artifacts=parent_artifacts if parent_artifacts else None,
        feature_dimensions=feature_dimensions,
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
