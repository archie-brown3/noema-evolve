"""
Thin adapters around the borrowed OpenEvolve components.

This package is the ONLY place in noema that touches openevolve internals
(see PLAN.md section 3.4, risk 6) — if openevolve is upgraded, the fallout is
contained here.
"""

from noema.substrate.base import (
    PopulationSnapshot,
    PopulationStore,
    Selection,
    SelectionPolicy,
    SubstrateRuntime,
)
from noema.substrate.database import SubstrateDatabase
from noema.substrate.islands import IslandsStore
from noema.substrate.evaluator import make_evaluator
from noema.substrate.prompts import build_mutation_prompt, inject_advice, make_prompt_sampler
from noema.substrate.views import ProgramView

__all__ = [
    "SubstrateDatabase",
    "IslandsStore",
    "PopulationStore",
    "SelectionPolicy",
    "SubstrateRuntime",
    "Selection",
    "PopulationSnapshot",
    "make_evaluator",
    "make_prompt_sampler",
    "build_mutation_prompt",
    "inject_advice",
    "ProgramView",
]
