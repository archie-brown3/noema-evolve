"""
noema: controlled ablation of coordination mechanisms in LLM-driven evolutionary search.

noema owns the top-level controller loop and borrows OpenEvolve's evaluator and
program database as libraries. Coordination mechanisms (e.g. HiFo-Prompt's insight
pool + navigator) are pluggable modules behind the CoordinationModule interface, so
coordination-present vs coordination-absent is a single controlled variable. All LLM
calls draw from a shared token budget ledger.

See PLAN.md at the repository root for the design document.
"""

from noema.budget.ledger import BudgetExhausted, CallRecord, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import NoemaConfig
from noema.controller import NoemaController
from noema.coordination import (
    Advice,
    CoordinationModule,
    GenerationContext,
    NullCoordination,
    build_coordination_module,
)

__all__ = [
    "Advice",
    "BudgetExhausted",
    "BudgetedLLM",
    "CallRecord",
    "CoordinationModule",
    "GenerationContext",
    "NoemaConfig",
    "NoemaController",
    "NullCoordination",
    "TokenLedger",
    "build_coordination_module",
]
