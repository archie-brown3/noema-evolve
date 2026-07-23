"""Pluggable coordination mechanisms (see PLAN.md section 3.1)"""

import logging
import random
from typing import Any, Dict, Optional

from noema.coordination.base import (
    Advice,
    CoordinationModule,
    GenerationContext,
    Intervention,
    NullCoordination,
    Outcome,
    ProposedProgram,
    SamplingRequest,
    SelectionContext,
)

from noema.coordination.bandit.module import BanditModule
from noema.coordination.hifo.module import HiFoPromptModule
from noema.coordination.pe.module import PunctuatedEquilibriumModule
from noema.coordination.pes.arms import PESCustomModule, PESFaithfulModule

logger = logging.getLogger(__name__)

# Registry of coordination arms, selected by NoemaConfig.coordination.module.
# Arm identity lives in the KEY: paired runs must differ in this one setting
# and nothing else (verify-run invariant, spec/LIVE-RUNS.md §4). The two PES
# variants therefore get their own keys rather than sharing "pes" plus params.
MODULE_REGISTRY: Dict[str, type] = {
    "null": NullCoordination,
    "hifo": HiFoPromptModule,
    "pes-custom": PESCustomModule,
    "pes-faithful": PESFaithfulModule,
    "bandit": BanditModule,
    "pe": PunctuatedEquilibriumModule,
}

# Deprecated alias -> canonical key. "pes" predates the split (task 0066) and
# meant today's custom behavior; existing run configs and RT-0002 still use it.
DEPRECATED_ALIASES: Dict[str, str] = {"pes": "pes-custom"}


def build_coordination_module(
    name: str,
    params: Optional[Dict[str, Any]] = None,
    llm=None,
    rng: Optional[random.Random] = None,
) -> CoordinationModule:
    """Instantiate a registered coordination module by registry key"""
    if name in DEPRECATED_ALIASES:
        canonical = DEPRECATED_ALIASES[name]
        logger.warning(
            f"coordination.module '{name}' is deprecated; use '{canonical}'. "
            f"Resolving to '{canonical}' (unchanged behavior)."
        )
        name = canonical
    try:
        module_cls = MODULE_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown coordination module '{name}'; available: {sorted(MODULE_REGISTRY)}"
        )
    return module_cls(config=params or {}, llm=llm, rng=rng)


__all__ = [
    "Advice",
    "Intervention",
    "ProposedProgram",
    "CoordinationModule",
    "GenerationContext",
    "NullCoordination",
    "Outcome",
    "SelectionContext",
    "SamplingRequest",
    "MODULE_REGISTRY",
    "DEPRECATED_ALIASES",
    "build_coordination_module",
]
