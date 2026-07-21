"""CVT-MAP-Elites population store for Noema.

A flat behavioural archive: the feature space (AST behaviour of a program's
source) is partitioned by a CVT (Voronoi tessellation whose sites are k-means++
centroids); each cell keeps the highest-fitness elite.  A substrate peer to
``IslandsStore`` and ``TreeStore``, selectable by ``substrate: cvt``.

Centroid maths and the behaviour archive are ported from LEVI
(https://github.com/ttanv/levi), MIT (c) 2025 Temoor Tanveer, ``levi/pool/
cvt_map_elites.py``.  NOEMA changes: implements the neutral ``PopulationStore``
Protocol (no LEVI pipeline / client / migration coupling), keeps the whole
program corpus (not just elites) for lineage + persistence, and seeds BOTH the
uniform sample draw and KMeans so centroid init is deterministic (the donor
seeds only KMeans, drawing samples from the global numpy RNG).
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import fields
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np
from sklearn.cluster import KMeans

from openevolve.database import Program
from openevolve.utils.metrics_utils import get_fitness_score

from noema.base import PopulationSnapshot, RegionSummary, Selection
from noema.cvt_behavior import DEFAULT_FEATURE_BOUNDS, BehaviorExtractor
from noema.views import ProgramView


_SCHEMA_VERSION = 1
_STATE_FILENAME = "cvt_store.json"
_BYTES_TAG = "__noema_cvt_bytes_base64__"
_PROGRAM_FIELDS = tuple(field.name for field in fields(Program))


def init_cvt_centroids(n_centroids: int, n_dims: int, seed: int) -> np.ndarray:
    """k-means++ centroids over a seeded uniform sample of [0, 1]^n_dims.

    Both the sample draw and KMeans are seeded, so the tessellation is a pure
    function of (n_centroids, n_dims, seed) — required for determinism/resume.
    """
    if n_centroids <= 0 or n_dims <= 0:
        raise ValueError("n_centroids and n_dims must be positive")
    rng = np.random.default_rng(seed)
    n_samples = max(10000, n_centroids * 10)
    samples = rng.uniform(0.0, 1.0, size=(n_samples, n_dims))
    kmeans = KMeans(
        n_clusters=n_centroids, init="k-means++", n_init=1, max_iter=100, random_state=seed
    )
    kmeans.fit(samples)
    return np.asarray(kmeans.cluster_centers_, dtype=float)


def nearest_centroid(centroids: np.ndarray, vec: np.ndarray) -> int:
    """Index of the nearest centroid by squared Euclidean distance."""
    distances = np.sum((centroids - vec) ** 2, axis=1)
    return int(np.argmin(distances))


def _json_encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {_BYTES_TAG: base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_json_encode(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("CVTStore state mappings require string keys")
        return {key: _json_encode(item) for key, item in value.items()}
    raise TypeError(f"CVTStore state cannot serialize {type(value).__name__}")


def _json_decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {_BYTES_TAG}:
            encoded = value[_BYTES_TAG]
            if not isinstance(encoded, str):
                raise ValueError("invalid CVTStore bytes artifact encoding")
            try:
                return base64.b64decode(encoded.encode("ascii"), validate=True)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid CVTStore bytes artifact encoding") from exc
        return {key: _json_decode(item) for key, item in value.items()}
    return value


class CVTStore:
    """A non-deleting behavioural archive: keeps every program, tracks the
    highest-fitness elite per CVT cell."""

    topology = "cvt_regions"
    capabilities = frozenset({"population", "elites", "fitness", "code", "regions", "cvt_cells"})

    def __init__(
        self,
        *,
        n_centroids: int = 256,
        behavior_features: Sequence[str] = ("math_operators", "loop_nesting_max",
                                            "comprehension_count", "range_max_arg"),
        feature_bounds: Optional[Mapping[str, tuple[float, float]]] = None,
        seed: int = 42,
        steps_per_generation: int = 1,
        feature_dimensions: Sequence[str] = (),
    ):
        if steps_per_generation <= 0:
            raise ValueError("steps_per_generation must be positive")
        self.steps_per_generation = int(steps_per_generation)
        self.feature_dimensions = tuple(feature_dimensions)  # openevolve fitness dims
        self.behavior_features = tuple(behavior_features)
        self.seed = int(seed)
        self.n_centroids = int(n_centroids)

        bounds = dict(DEFAULT_FEATURE_BOUNDS)
        if feature_bounds:
            bounds.update(feature_bounds)
        self._extractor = BehaviorExtractor(list(self.behavior_features))
        self._extractor.set_fixed_bounds(bounds)
        self._feature_bounds = {f: bounds[f] for f in self.behavior_features}

        self._centroids = init_cvt_centroids(self.n_centroids, len(self.behavior_features), self.seed)
        self._programs: Dict[str, Program] = {}
        self._cell_of: Dict[str, int] = {}          # program id -> its cell
        self._elite_of: Dict[int, str] = {}         # cell -> current elite program id
        self._artifacts: Dict[str, Dict[str, Any]] = {}
        self.last_iteration = 0

    # -- basic properties ---------------------------------------------------

    @property
    def num_programs(self) -> int:
        return len(self._programs)

    def target_scope(self, iteration: int) -> None:
        # The child's cell is decided by its behaviour at add() time, not chosen
        # up front; parent selection is the composed policy's job.
        return None

    def _cell_vector(self, program: Program) -> np.ndarray:
        fv = self._extractor.extract(program.code)
        return np.asarray(fv.to_array(list(self.behavior_features)), dtype=float)

    def cell_of(self, program: Program) -> int:
        return nearest_centroid(self._centroids, self._cell_vector(program))

    def _fitness_for(self, program: Program) -> float:
        return float(get_fitness_score(program.metrics, list(self.feature_dimensions)))

    def fitness(self, program: Program) -> float:
        return self._fitness_for(program)

    def _sorted(self, programs: Sequence[Program]) -> tuple[Program, ...]:
        return tuple(sorted(programs, key=lambda p: (-self.fitness(p), p.id)))

    # -- reads --------------------------------------------------------------

    def _elite_programs(self) -> tuple[Program, ...]:
        return tuple(self._programs[pid] for pid in self._elite_of.values())

    def population(self, scope: Any = None) -> Sequence[Program]:
        if scope is None:
            return tuple(self._programs[k] for k in sorted(self._programs))
        return tuple(
            self._programs[pid] for pid, cell in sorted(self._cell_of.items()) if cell == scope
        )

    def top_programs(self, n: int, scope: Any = None) -> Sequence[Program]:
        if n <= 0:
            return ()
        candidates = self._elite_programs() if scope is None else self.population(scope)
        return self._sorted(candidates)[:n]

    def elites(self, scope: Any = None) -> Sequence[Program]:
        if scope is None:
            return self._sorted(self._elite_programs())
        return self.top_programs(1, scope)

    def best_program(self) -> Optional[Program]:
        ranked = self.top_programs(1)
        return ranked[0] if ranked else None

    def all_fitnesses(self) -> Sequence[float]:
        return tuple(self.fitness(p) for p in self.population())

    def regions(self) -> Sequence[RegionSummary]:
        summaries = []
        for cell in sorted(self._elite_of):
            elite = self._programs[self._elite_of[cell]]
            summaries.append(
                RegionSummary(
                    scope=cell,
                    label=f"cell:{cell}",
                    best_fitness=self.fitness(elite),
                    size=sum(1 for c in self._cell_of.values() if c == cell),
                )
            )
        return tuple(summaries)

    def per_scope_bests(self) -> Sequence[float]:
        return tuple(region.best_fitness for region in self.regions())

    def view(self, program: Program) -> ProgramView:
        return ProgramView.from_program(program, list(self.feature_dimensions))

    def views(self, programs: Sequence[Program]) -> Sequence[ProgramView]:
        return tuple(self.view(p) for p in programs)

    def snapshot(self, scope: Any = None, limit: Optional[int] = None) -> PopulationSnapshot:
        count = limit if limit is not None else (
            len(self._elite_of) if scope is None else len(self.population(scope))
        )
        programs = self.top_programs(count, scope)
        views = self.views(programs)
        fit_source = self._elite_programs() if scope is None else self.population(scope)
        return PopulationSnapshot(
            scope=scope,
            top_programs=tuple(views),
            fitnesses=tuple(self.fitness(p) for p in self._sorted(fit_source)),
            best_program=views[0] if views else None,
            topology=self.topology,
            regions=tuple(self.regions()) if scope is None else (),
        )

    # -- writes -------------------------------------------------------------

    def add(self, program: Program, iteration: Optional[int] = None, target_scope: Any = None) -> str:
        if not isinstance(program, Program):
            raise TypeError("CVTStore accepts openevolve.database.Program instances only")
        if not program.id:
            raise ValueError("CVTStore program IDs must be non-empty")
        if program.id in self._programs:
            raise ValueError(f"duplicate CVTStore program ID: {program.id}")

        cell = self.cell_of(program)
        self._programs[program.id] = program
        self._cell_of[program.id] = cell
        incumbent = self._elite_of.get(cell)
        if incumbent is None or self.fitness(program) > self.fitness(self._programs[incumbent]):
            self._elite_of[cell] = program.id
        if iteration is not None:
            self.last_iteration = int(iteration)
        return program.id

    def store_artifacts(self, program_id: str, artifacts: Mapping[str, Any]) -> None:
        if program_id not in self._programs:
            raise ValueError(f"cannot store artifacts for unknown program: {program_id}")
        self._artifacts[program_id] = dict(artifacts)

    def native_select(self, target_scope: Any, num_inspirations: int) -> Selection:
        raise RuntimeError(
            "CVTStore has no native selection; compose it with a selection policy"
        )

    def end_generation(self) -> bool:
        return False

    # -- checkpoint ---------------------------------------------------------

    @staticmethod
    def _program_state(program: Program) -> Dict[str, Any]:
        return {name: _json_encode(getattr(program, name)) for name in _PROGRAM_FIELDS}

    @staticmethod
    def _program_from_state(state: Mapping[str, Any]) -> Program:
        if not isinstance(state, Mapping) or set(state) != set(_PROGRAM_FIELDS):
            raise ValueError("invalid serialized Program fields")
        try:
            return Program(**{name: _json_decode(state[name]) for name in _PROGRAM_FIELDS})
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid serialized Program") from exc

    def state_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "last_iteration": self.last_iteration,
            "steps_per_generation": self.steps_per_generation,
            "seed": self.seed,
            "n_centroids": self.n_centroids,
            "behavior_features": list(self.behavior_features),
            "feature_bounds": {f: list(self._feature_bounds[f]) for f in self.behavior_features},
            "feature_dimensions": list(self.feature_dimensions),
            "centroids": self._centroids.tolist(),
            "programs": {k: self._program_state(self._programs[k]) for k in sorted(self._programs)},
            "cell_of": {k: self._cell_of[k] for k in sorted(self._cell_of)},
            "elite_of": {str(cell): self._elite_of[cell] for cell in sorted(self._elite_of)},
            "artifacts": _json_encode({k: self._artifacts[k] for k in sorted(self._artifacts)}),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping) or state.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported or invalid CVTStore state")
        try:
            self.steps_per_generation = int(state["steps_per_generation"])
            self.seed = int(state["seed"])
            self.n_centroids = int(state["n_centroids"])
            self.behavior_features = tuple(state["behavior_features"])
            self._feature_bounds = {f: tuple(b) for f, b in state["feature_bounds"].items()}
            self.feature_dimensions = tuple(state["feature_dimensions"])
            self._centroids = np.asarray(state["centroids"], dtype=float)
            self.last_iteration = int(state["last_iteration"])
            programs = {str(k): self._program_from_state(v) for k, v in state["programs"].items()}
            cell_of = {str(k): int(v) for k, v in state["cell_of"].items()}
            elite_of = {int(cell): str(pid) for cell, pid in state["elite_of"].items()}
            artifacts = _json_decode(state["artifacts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid CVTStore state shape") from exc
        if set(cell_of) != set(programs):
            raise ValueError("CVTStore cell map must cover exactly the programs")
        if any(pid not in programs for pid in elite_of.values()):
            raise ValueError("CVTStore elite refers to an unknown program")
        if self._centroids.shape != (self.n_centroids, len(self.behavior_features)):
            raise ValueError("CVTStore centroid shape inconsistent with config")
        self._extractor = BehaviorExtractor(list(self.behavior_features))
        self._extractor.set_fixed_bounds(dict(self._feature_bounds))
        self._programs = programs
        self._cell_of = cell_of
        self._elite_of = elite_of
        self._artifacts = {str(k): dict(v) for k, v in artifacts.items()}

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
            raise ValueError(f"cannot load CVTStore checkpoint: {target}") from exc
        self.load_state_dict(state)
