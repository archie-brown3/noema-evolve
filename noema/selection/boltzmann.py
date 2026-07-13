"""LoongFlow 0.0.1 Boltzmann parent selection.

Copied/adapted from
``loongflow/agentsdk/memory/evolution/boltzmann.py`` (Apache-2.0), wheel
SHA-256 cdc0bc9b9f6339e4517ffc6847040de4681da8d992d41da4b33642eb53ce2493.
Released-code quirks are intentionally preserved by the kernel helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from noema.base import PopulationStore, Selection


def _rng(rng=None):
    return rng if rng is not None else np.random


def _code(solution) -> str:
    return (getattr(solution, "solution", None) or getattr(solution, "code", None) or "")


def _score(solution) -> float:
    value = getattr(solution, "score", None)
    if value is None:
        value = getattr(solution, "fitness", None)
    if value is None and hasattr(solution, "metrics"):
        value = solution.metrics.get("combined_score", 0.0)
    return float(value or 0.0)


def _weight(solution) -> float:
    value = getattr(solution, "sample_weight", None)
    if value is None and hasattr(solution, "metadata"):
        value = solution.metadata.get("sample_weight")
    return float(value or 1.0)


def _calculate_diversity(solutions, sample_size: int = 50, rng=None) -> float:
    if not solutions:
        raise ValueError("Cannot calculate diversity of empty solutions list")
    if len(solutions) <= 1:
        return 0.0
    generator = _rng(rng)
    indices = generator.choice(
        len(solutions), size=min(sample_size, len(solutions)), replace=False
    )
    sampled = [solutions[int(index)] for index in indices]
    scores = []
    for i, first in enumerate(sampled):
        for second in sampled[i + 1 :]:
            first_code, second_code = _code(first), _code(second)
            len_diff = abs(len(first_code) - len(second_code)) / max(
                1, max(len(first_code), len(second_code))
            )
            line_diff = abs(first_code.count("\n") - second_code.count("\n")) / max(
                1, max(first_code.count("\n"), second_code.count("\n"))
            )
            first_chars, second_chars = set(first_code), set(second_code)
            char_diff = len(first_chars.symmetric_difference(second_chars)) / max(
                1, max(len(first_chars), len(second_chars))
            )
            scores.append(0.4 * len_diff + 0.3 * line_diff + 0.3 * char_diff)
    return float(np.mean(scores)) if scores else 0.0


def _adaptive_temperature_by_diversity(
    current_temp: float,
    diversity: float,
    min_temp: float = 0.5,
    max_temp: float = 2.0,
    base_temp: float = 1.0,
) -> float:
    adjustment_factor = 1 + (2 * diversity - 1)
    new_temp = max(min_temp, min(max_temp, base_temp * adjustment_factor))
    return 0.8 * new_temp + 0.2 * current_temp


def _candidate_pool_released(solutions, elites, rng=None):
    """Released 0.0.1 candidate construction, including its ID/object bug."""
    generator = _rng(rng)
    non_elites = [s for s in solutions if getattr(s, "solution_id", None) not in elites]
    candidates = []
    if elites:
        if len(elites) >= 3:
            indices = generator.choice(len(elites), size=3, replace=False)
            candidates.extend(elites[int(i)] for i in indices)
        else:
            candidates.extend(elites)
    if non_elites:
        if len(non_elites) >= 2:
            indices = generator.choice(len(non_elites), size=2, replace=False)
            candidates.extend(non_elites[int(i)] for i in indices)
        else:
            candidates.extend(non_elites)
    if len(candidates) < 5:
        needed = 5 - len(candidates)
        source = elites if len(elites) > len(non_elites) else non_elites
        available = [item for item in source if item not in candidates]
        if len(available) >= needed:
            indices = generator.choice(len(available), size=needed, replace=False)
            candidates.extend(available[int(i)] for i in indices)
        else:
            candidates.extend(available)
    return candidates


def _combined_probabilities(
    candidates, temperature: float, use_sampling_weight: bool = True,
    sampling_weight_power: float = 1.0,
):
    scores = np.array([_score(item) for item in candidates], dtype=float)
    probabilities = np.exp((scores - np.max(scores)) / temperature)
    if use_sampling_weight:
        weights = np.array([_weight(item) for item in candidates], dtype=float)
        if sampling_weight_power != 1.0:
            weights = np.power(weights, sampling_weight_power)
        probabilities *= weights
    probabilities = np.nan_to_num(probabilities, nan=0.0, posinf=0.0, neginf=0.0)
    probabilities = np.clip(probabilities, 0.0, None)
    total = float(np.sum(probabilities))
    return probabilities / total if total > 0 else probabilities


def _boltzmann_selection_with_weights(
    solutions,
    elites,
    temperature: float,
    use_sampling_weight: bool = True,
    sampling_weight_power: float = 1.0,
    exploration_rate: float = 0.1,
    rng=None,
):
    if not solutions:
        raise ValueError("Cannot select from empty solutions list")
    if temperature <= 0:
        raise ValueError("Temperature must be positive")
    if not 0 <= exploration_rate <= 1:
        raise ValueError("exploration_rate must be between 0 and 1")
    generator = _rng(rng)
    if generator.random() < exploration_rate:
        return generator.choice(solutions)
    candidates = _candidate_pool_released(solutions, elites, generator)
    if not candidates:
        return None
    probabilities = _combined_probabilities(
        candidates, temperature, use_sampling_weight, sampling_weight_power
    )
    if float(np.sum(probabilities)) > 0:
        return candidates[int(generator.choice(len(candidates), p=probabilities))]
    return max(candidates, key=_score)


def select_parents_with_dynamic_temperature(
    solutions,
    elites,
    initial_temp: float,
    min_temp: float = 0.5,
    max_temp: float = 2.0,
    use_sampling_weight: bool = True,
    sampling_weight_power: float = 1.0,
    exploration_rate: float = 0.2,
    rng=None,
):
    diversity = _calculate_diversity(solutions, rng=rng)
    temperature = _adaptive_temperature_by_diversity(
        initial_temp, diversity, min_temp, max_temp, initial_temp
    )
    return _boltzmann_selection_with_weights(
        solutions,
        elites,
        temperature,
        use_sampling_weight,
        sampling_weight_power,
        exploration_rate,
        rng,
    )


def _stagnation_adjusted_exploration_rate(
    base_rate: float, recent_scores: Sequence[float], mode: str = "released"
) -> float:
    if mode != "released":
        raise ValueError(f"unknown stagnation mode {mode!r}")
    differences = [
        abs(recent_scores[index] - recent_scores[index - 1])
        for index in range(1, len(recent_scores))
    ]
    rate = base_rate
    if all(difference < 0.01 for difference in differences):
        rate *= 2
    elif all(difference < 0.001 for difference in differences):
        rate *= 4
    return 0.9 if rate >= 1 else rate


@dataclass
class _ProgramCandidate:
    original: Any
    score: float

    @property
    def solution(self):
        return self.original.code

    @property
    def solution_id(self):
        return self.original.id

    @property
    def sample_weight(self):
        return self.original.metadata.get("sample_weight", 1.0)


class BoltzmannSelectionPolicy:
    required_capabilities = frozenset(
        {"population", "elites", "fitness", "code", "sampling_weights"}
    )
    supported_hints = frozenset()

    def __init__(
        self,
        rng=None,
        temperature: float = 1.0,
        exploration_rate: float = 0.2,
        stagnation_mode: str = "released",
        stagnation_enabled: bool = False,
    ):
        self.rng = rng or np.random.RandomState()
        self.temperature = temperature
        self.exploration_rate = exploration_rate
        self.stagnation_mode = stagnation_mode
        self.stagnation_enabled = stagnation_enabled
        self.recent_scores = []
        self.weighted_inspiration_draws = 0

    @classmethod
    def from_config(cls, config):
        return cls(
            rng=np.random.RandomState(config.seed),
            temperature=config.boltzmann_temperature,
            exploration_rate=config.boltzmann_exploration_rate,
            stagnation_mode=config.stagnation_mode,
            stagnation_enabled=config.stagnation_detection_enabled,
        )

    def select(
        self,
        store_or_population,
        *,
        target_scope=None,
        fallback=None,
        elites=None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection:
        store = store_or_population if isinstance(store_or_population, PopulationStore) else None
        if store is not None:
            originals = list(store.population(target_scope))
            fallback_originals = list(store.population(None))
            elite_originals = list(store.elites(target_scope))
            adapters = {
                program.id: _ProgramCandidate(program, store.fitness(program))
                for program in fallback_originals
            }
            population = [adapters[program.id] for program in originals]
            fallback_population = [adapters[program.id] for program in fallback_originals]
            elite_population = [adapters[program.id] for program in elite_originals]
        else:
            population = list(store_or_population)
            fallback_population = list(fallback or population)
            elite_population = list(elites or ())

        active = population or fallback_population
        if not active:
            raise ValueError("Cannot select from an empty population and fallback")
        rate = self.exploration_rate
        if self.stagnation_enabled:
            rate = _stagnation_adjusted_exploration_rate(
                rate, self.recent_scores[-5:], self.stagnation_mode
            )
        selected = select_parents_with_dynamic_temperature(
            active,
            elite_population,
            self.temperature,
            exploration_rate=rate,
            rng=self.rng,
        )
        parent = selected.original if isinstance(selected, _ProgramCandidate) else selected
        inspiration_pool = [item for item in active if item is not selected]
        count = min(num_inspirations, len(inspiration_pool))
        if count:
            indices = self.rng.choice(len(inspiration_pool), size=count, replace=False)
            chosen = [inspiration_pool[int(index)] for index in indices]
        else:
            chosen = []
        inspirations = tuple(
            item.original if isinstance(item, _ProgramCandidate) else item for item in chosen
        )
        source_scope = getattr(parent, "metadata", {}).get("island", target_scope)
        return Selection(parent, inspirations, source_scope, target_scope)

    def on_child_accepted(self, *, parent, child, step_size: float) -> None:
        parent_score, child_score = _score(parent), _score(child)
        parent_weight = _weight(parent)
        child_weight = max(
            0.05,
            parent_weight + 3 * (child_score - parent_score) * step_size + 3 * child_score,
        )
        if hasattr(parent, "sample_cnt"):
            parent.sample_cnt += 1
        else:
            parent.metadata["sample_cnt"] = int(parent.metadata.get("sample_cnt", 0)) + 1
        if hasattr(child, "sample_weight"):
            child.sample_weight = child_weight
        else:
            child.metadata["sample_weight"] = child_weight
            child.metadata.setdefault("sample_cnt", 0)
        self.recent_scores.append(child_score)
        self.recent_scores = self.recent_scores[-5:]

    def on_child_rejected(self, *, parent, child=None, eval_failed: bool) -> None:
        return None

    def state_dict(self) -> Dict[str, Any]:
        state = self.rng.get_state()
        return {
            "temperature": self.temperature,
            "exploration_rate": self.exploration_rate,
            "stagnation_mode": self.stagnation_mode,
            "stagnation_enabled": self.stagnation_enabled,
            "recent_scores": list(self.recent_scores),
            "rng_state": [state[0], state[1].tolist(), state[2], state[3], state[4]],
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if not state:
            return
        self.temperature = float(state["temperature"])
        self.exploration_rate = float(state["exploration_rate"])
        self.stagnation_mode = state["stagnation_mode"]
        self.stagnation_enabled = bool(state["stagnation_enabled"])
        self.recent_scores = list(state["recent_scores"])
        rng_state = state["rng_state"]
        self.rng.set_state(
            (rng_state[0], np.array(rng_state[1], dtype=np.uint32), *rng_state[2:])
        )
