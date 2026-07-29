"""Bounded, global population storage for source-compatible HiFo experiments."""

from __future__ import annotations

import base64
import json
import math
import os
from dataclasses import fields
from typing import Any, Dict, Mapping, Optional, Sequence

from openevolve.database import Program
from openevolve.utils.metrics_utils import get_fitness_score

from noema.evolution.views import ProgramView
from noema.substrates.base import PopulationSnapshot, RegionSummary, Selection


_SCHEMA_VERSION = 1
_BYTES_TAG = "__noema_flat_bytes_base64__"
_PROGRAM_FIELDS = tuple(field.name for field in fields(Program))
_STATE_FILENAME = "flat_store.json"


def _json_encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {_BYTES_TAG: base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_json_encode(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("FlatPopulationStore state mappings require string keys")
        return {key: _json_encode(item) for key, item in value.items()}
    raise TypeError(f"FlatPopulationStore state cannot serialize {type(value).__name__}")


def _json_decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {_BYTES_TAG}:
            encoded = value[_BYTES_TAG]
            if not isinstance(encoded, str):
                raise ValueError("invalid FlatPopulationStore bytes encoding")
            try:
                return base64.b64decode(encoded.encode("ascii"), validate=True)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid FlatPopulationStore bytes encoding") from exc
        return {key: _json_decode(item) for key, item in value.items()}
    return value


class FlatPopulationStore:
    """Keep the best distinct-fitness programs in one global population."""

    topology = "flat"
    capabilities = frozenset({"flat_population", "population", "fitness", "code"})

    def __init__(
        self,
        population_size: int,
        *,
        steps_per_generation: int = 1,
        feature_dimensions: Sequence[str] = (),
    ):
        if population_size <= 0:
            raise ValueError("population_size must be positive")
        if steps_per_generation <= 0:
            raise ValueError("steps_per_generation must be positive")
        self.population_size = int(population_size)
        self.steps_per_generation = int(steps_per_generation)
        self.feature_dimensions = tuple(feature_dimensions)
        self._programs: Dict[str, Program] = {}
        self._artifacts: Dict[str, Dict[str, Any]] = {}
        self.last_iteration = 0

    @property
    def num_programs(self) -> int:
        return len(self._programs)

    def target_scope(self, iteration: int) -> None:
        return None

    def fitness(self, program: Program) -> float:
        return float(get_fitness_score(program.metrics, list(self.feature_dimensions)))

    def population(self, scope: Any = None) -> Sequence[Program]:
        if scope is not None:
            return ()
        return tuple(
            sorted(self._programs.values(), key=lambda program: -self.fitness(program))
        )

    def top_programs(self, n: int, scope: Any = None) -> Sequence[Program]:
        if n <= 0 or scope is not None:
            return ()
        return self.population()[:n]

    def elites(self, scope: Any = None) -> Sequence[Program]:
        return self.top_programs(10, scope)

    def best_program(self) -> Optional[Program]:
        ranked = self.top_programs(1)
        return ranked[0] if ranked else None

    def all_fitnesses(self) -> Sequence[float]:
        return tuple(self.fitness(program) for program in self.population())

    def regions(self) -> Sequence[RegionSummary]:
        return ()

    def per_scope_bests(self) -> Sequence[float]:
        return ()

    def view(self, program: Program) -> ProgramView:
        return ProgramView.from_program(program, list(self.feature_dimensions))

    def views(self, programs: Sequence[Program]) -> Sequence[ProgramView]:
        return tuple(self.view(program) for program in programs)

    def snapshot(
        self, scope: Any = None, limit: Optional[int] = None
    ) -> PopulationSnapshot:
        programs = self.population(scope)
        if limit is not None:
            programs = programs[:limit]
        views = self.views(programs)
        return PopulationSnapshot(
            scope=scope,
            top_programs=tuple(views),
            fitnesses=self.all_fitnesses() if scope is None else (),
            best_program=views[0] if views else None,
            topology=self.topology,
            regions=(),
        )

    def add(
        self,
        program: Program,
        iteration: Optional[int] = None,
        target_scope: Any = None,
    ) -> Optional[str]:
        if not isinstance(program, Program):
            raise TypeError("FlatPopulationStore accepts openevolve.database.Program instances only")
        if not program.id:
            raise ValueError("FlatPopulationStore program IDs must be non-empty")
        if program.id in self._programs:
            raise ValueError(f"duplicate FlatPopulationStore program ID: {program.id}")
        score = self.fitness(program)
        if not math.isfinite(score):
            raise ValueError("FlatPopulationStore requires finite program fitness")

        ranked = sorted(
            [*self._programs.values(), program], key=lambda item: -self.fitness(item)
        )
        retained: Dict[str, Program] = {}
        seen_scores = set()
        for candidate in ranked:
            candidate_score = self.fitness(candidate)
            if candidate_score in seen_scores:
                continue
            retained[candidate.id] = candidate
            seen_scores.add(candidate_score)
            if len(retained) == self.population_size:
                break
        evicted_ids = set(self._programs) - set(retained)
        self._programs = retained
        for program_id in evicted_ids:
            self._artifacts.pop(program_id, None)
        if iteration is not None:
            self.last_iteration = int(iteration)
        return program.id if program.id in retained else None

    def store_artifacts(self, program_id: str, artifacts: Mapping[str, Any]) -> None:
        if program_id not in self._programs:
            raise ValueError(f"cannot store artifacts for unknown program: {program_id}")
        self._artifacts[program_id] = dict(artifacts)

    def native_select(self, target_scope: Any, num_inspirations: int) -> Selection:
        raise RuntimeError(
            "FlatPopulationStore has no native selection; compose it with a selection policy"
        )

    def end_generation(self) -> bool:
        return False

    @staticmethod
    def _program_state(program: Program) -> Dict[str, Any]:
        return {
            field_name: _json_encode(getattr(program, field_name))
            for field_name in _PROGRAM_FIELDS
        }

    @staticmethod
    def _program_from_state(state: Mapping[str, Any]) -> Program:
        if set(state) != set(_PROGRAM_FIELDS):
            raise ValueError("invalid serialized Program fields")
        try:
            return Program(
                **{
                    field_name: _json_decode(state[field_name])
                    for field_name in _PROGRAM_FIELDS
                }
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized Program") from exc

    def state_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "population_size": self.population_size,
            "steps_per_generation": self.steps_per_generation,
            "feature_dimensions": list(self.feature_dimensions),
            "last_iteration": self.last_iteration,
            "programs": {
                program_id: self._program_state(program)
                for program_id, program in self._programs.items()
            },
            "artifacts": _json_encode(self._artifacts),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise ValueError("FlatPopulationStore state must be a mapping")
        if state.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported FlatPopulationStore state version")
        try:
            population_size = int(state["population_size"])
            steps_per_generation = int(state["steps_per_generation"])
            feature_dimensions = tuple(state["feature_dimensions"])
            last_iteration = int(state["last_iteration"])
            raw_programs = state["programs"]
            raw_artifacts = state["artifacts"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid FlatPopulationStore state shape") from exc
        if population_size <= 0 or steps_per_generation <= 0 or last_iteration < 0:
            raise ValueError("invalid FlatPopulationStore numeric state")
        if not all(isinstance(name, str) for name in feature_dimensions):
            raise ValueError("FlatPopulationStore feature dimensions must be strings")
        if not isinstance(raw_programs, Mapping) or not isinstance(raw_artifacts, Mapping):
            raise ValueError("invalid FlatPopulationStore program state")
        if not all(
            isinstance(program_id, str) and isinstance(program_state, Mapping)
            for program_id, program_state in raw_programs.items()
        ):
            raise ValueError("invalid FlatPopulationStore program state")
        artifacts = _json_decode(raw_artifacts)
        if not isinstance(artifacts, Mapping) or any(
            not isinstance(value, Mapping) for value in artifacts.values()
        ):
            raise ValueError("FlatPopulationStore artifacts must be mappings by program ID")
        programs = {
            program_id: self._program_from_state(program_state)
            for program_id, program_state in raw_programs.items()
        }
        if any(program.id != program_id for program_id, program in programs.items()):
            raise ValueError("serialized Program ID does not match its state key")
        if len(programs) > population_size:
            raise ValueError("FlatPopulationStore state exceeds population size")
        if not set(artifacts).issubset(programs):
            raise ValueError("FlatPopulationStore artifacts refer to unknown programs")

        self.population_size = population_size
        self.steps_per_generation = steps_per_generation
        self.feature_dimensions = feature_dimensions
        self._programs = programs
        self._artifacts = {program_id: dict(value) for program_id, value in artifacts.items()}
        self.last_iteration = last_iteration

    def save(self, path: str, iteration: int = 0) -> None:
        self.last_iteration = int(iteration)
        os.makedirs(path, exist_ok=True)
        target = os.path.join(path, _STATE_FILENAME)
        temporary = f"{target}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.state_dict(), handle, sort_keys=True, separators=(",", ":"))
        os.replace(temporary, target)

    def load(self, path: str) -> None:
        target = os.path.join(path, _STATE_FILENAME)
        try:
            with open(target, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load FlatPopulationStore checkpoint: {target}") from exc
        self.load_state_dict(state)
