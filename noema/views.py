"""
Read-only program views handed to coordination modules.

Coordination modules never see openevolve's mutable Program objects — they get
frozen snapshots so a module cannot corrupt database state, and so the interface
they depend on is noema's, not openevolve's.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openevolve.database import Program
from openevolve.utils.metrics_utils import get_fitness_score


@dataclass(frozen=True)
class ProgramView:
    """Immutable snapshot of a program for coordination modules"""

    id: str
    code: str
    fitness: float  # combined_score if present, else avg of non-feature metrics
    generation: int = 0
    iteration_found: int = 0
    changes_description: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_program(cls, program: Program, feature_dimensions: Optional[List[str]] = None):
        return cls(
            id=program.id,
            code=program.code,
            fitness=get_fitness_score(program.metrics, feature_dimensions or []),
            generation=program.generation,
            iteration_found=program.iteration_found,
            changes_description=program.changes_description or "",
            metrics=dict(program.metrics),
            metadata=dict(program.metadata),
        )
