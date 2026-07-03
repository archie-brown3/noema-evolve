"""Pluggable coordination mechanisms (see PLAN.md section 3.1)"""

import random
from typing import Any, Dict, Optional

from noema.coordination.base import (
    Advice,
    CoordinationModule,
    GenerationContext,
    NullCoordination,
)

# Registry of coordination arms; mechanisms register themselves on import
MODULE_REGISTRY: Dict[str, type] = {
    "null": NullCoordination,
}


def build_coordination_module(
    name: str,
    params: Optional[Dict[str, Any]] = None,
    llm=None,
    rng: Optional[random.Random] = None,
) -> CoordinationModule:
    """Instantiate a registered coordination module by registry key"""
    try:
        module_cls = MODULE_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown coordination module '{name}'; available: {sorted(MODULE_REGISTRY)}"
        )
    return module_cls(config=params or {}, llm=llm, rng=rng)


__all__ = [
    "Advice",
    "CoordinationModule",
    "GenerationContext",
    "NullCoordination",
    "MODULE_REGISTRY",
    "build_coordination_module",
]
