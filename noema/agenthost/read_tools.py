"""Inner-session read snapshot for deep CLI sessions (task 0175 / option-2 fix).

Serializes the store's canonical ``PopulationSnapshot`` (same projection the
shared iteration runner hands to coordination) plus an explicit lineage
adjacency map built from ``Program.parent_id``. ``ProgramView`` stays frozen —
lineage is a deep-arm side channel, not a forged metadata key.
"""

from __future__ import annotations

from typing import Any, Dict

from noema.evolution.views import ProgramView
from noema.substrates.base import PopulationSnapshot, RegionSummary


def _view_dict(view: ProgramView) -> Dict[str, Any]:
    return {
        "id": view.id,
        "code": view.code,
        "fitness": view.fitness,
        "generation": view.generation,
        "iteration_found": view.iteration_found,
        "changes_description": view.changes_description,
        "metrics": dict(view.metrics),
        "metadata": dict(view.metadata),
    }


def _region_dict(region: RegionSummary) -> Dict[str, Any]:
    return {
        "scope": region.scope,
        "label": region.label,
        "best_fitness": region.best_fitness,
        "size": region.size,
    }


def _population_dict(population: PopulationSnapshot) -> Dict[str, Any]:
    top = [_view_dict(view) for view in population.top_programs]
    best = _view_dict(population.best_program) if population.best_program else None
    return {
        "scope": population.scope,
        "topology": population.topology,
        "fitnesses": list(population.fitnesses),
        "regions": [_region_dict(region) for region in population.regions],
        "best_program": best,
        "top_programs": top,
    }


def build_snapshot(session) -> Dict[str, Any]:
    """Serialize population + lineage + coordination for tools/snapshot.json."""
    store = session.store
    # Match generation-tick GenerationContext: full global view (limit=None).
    population = store.snapshot(None, limit=None)
    population_dict = _population_dict(population)
    # The active snapshot may intentionally be smaller than the retained corpus
    # (Tree working set; CVT elites).  Program lookup and lineage therefore use
    # one captured retained set and the store's own view adapter.
    retained = tuple(store.population())
    programs = {view.id: _view_dict(view) for view in store.views(retained)}
    lineage = {program.id: program.parent_id for program in retained}
    return {
        "run_status": session.run_status(),
        "coordination": session.coordination.log_snapshot(),
        "population": population_dict,
        "lineage": lineage,
        "programs": programs,
    }
