"""HiFo's probability-rank parent selection for a bounded flat population."""

from __future__ import annotations

import random
from typing import Any, Dict, Mapping, Optional

from noema.substrates.base import PopulationStore, Selection


def _tuplify(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuplify(item) for item in value)
    return value


class HiFoProbRankSelection:
    """Sample one parent with HiFo's rank-weighted, replacement semantics."""

    required_capabilities = frozenset({"flat_population", "population", "fitness"})
    supported_hints = frozenset()

    def __init__(self, random_seed: Optional[int] = None):
        self.rng = random.Random(random_seed)

    def select(
        self,
        store: PopulationStore,
        *,
        target_scope=None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection:
        population = list(store.population())
        if not population:
            raise ValueError("cannot select from an empty flat population")
        population.sort(key=store.fitness, reverse=True)
        size = len(population)
        weights = [1 / (rank + 1 + size) for rank in range(size)]
        parent = self.rng.choices(population, weights=weights, k=1)[0]
        return Selection(parent, (), None, None)

    def on_child_accepted(self, *, parent, child, step_size: float) -> None:
        return None

    def on_child_rejected(self, *, parent, child=None, eval_failed: bool) -> None:
        return None

    def state_dict(self) -> Dict[str, Any]:
        return {"rng_state": self.rng.getstate()}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if state:
            try:
                self.rng.setstate(_tuplify(state["rng_state"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid HiFo probability-rank state") from exc
