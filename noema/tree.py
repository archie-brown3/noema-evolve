"""Persistent global-tree population storage for Noema.

The topology is adapted from MCTS-AHD at commit
ee9c4f424503c65a5fd2b899e6620ce86079fedb (MIT), ``source/mcts.py``.

NOEMA: the donor combines heuristic payloads, tree topology, Q/N statistics,
selection, and expansion.  ``TreeStore`` owns only real ``Program`` payloads,
permanent lineage, bounded working context, artifacts, and persistence.  UCT
state and parent selection belong to task 0037's separate policy.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import fields
from typing import Any, Dict, Mapping, Optional, Sequence

from openevolve.database import Program
from openevolve.utils.metrics_utils import get_fitness_score

from noema.base import PopulationSnapshot, RegionSummary, Selection
from noema.views import ProgramView


_SCHEMA_VERSION = 1
_STATE_FILENAME = "tree_store.json"
_BYTES_TAG = "__noema_tree_bytes_base64__"
_PROGRAM_FIELDS = tuple(field.name for field in fields(Program))


def _json_encode(value: Any) -> Any:
    """Return a JSON-safe copy, preserving bytes with an explicit tag."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {_BYTES_TAG: base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_json_encode(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("TreeStore state mappings require string keys")
        return {key: _json_encode(item) for key, item in value.items()}
    raise TypeError(f"TreeStore state cannot serialize {type(value).__name__}")


def _json_decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_json_decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {_BYTES_TAG}:
            encoded = value[_BYTES_TAG]
            if not isinstance(encoded, str):
                raise ValueError("invalid TreeStore bytes artifact encoding")
            try:
                return base64.b64decode(encoded.encode("ascii"), validate=True)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid TreeStore bytes artifact encoding") from exc
        return {key: _json_decode(item) for key, item in value.items()}
    return value


class TreeStore:
    """A non-deleting global program tree with bounded prompt context."""

    topology = "tree_branches"
    capabilities = frozenset({"population", "elites", "fitness", "code", "regions"})

    def __init__(
        self,
        steps_per_generation: int = 1,
        *,
        working_set_size: int = 10,
        feature_dimensions: Sequence[str] = (),
    ):
        if steps_per_generation <= 0:
            raise ValueError("steps_per_generation must be positive")
        if working_set_size <= 0:
            raise ValueError("working_set_size must be positive")
        self.steps_per_generation = int(steps_per_generation)
        self.working_set_size = int(working_set_size)
        self.feature_dimensions = tuple(feature_dimensions)
        self._programs: Dict[str, Program] = {}
        self._parents: Dict[str, Optional[str]] = {}
        self._children: Dict[str, list[str]] = {}
        self._branches: Dict[str, str] = {}
        self._artifacts: Dict[str, Dict[str, Any]] = {}
        self._working_ids: list[str] = []
        self._trunk_id: Optional[str] = None
        self.last_iteration = 0

    @property
    def num_programs(self) -> int:
        return len(self._programs)

    @property
    def trunk_scope(self) -> Optional[str]:
        return None if self._trunk_id is None else f"trunk:{self._trunk_id}"

    def target_scope(self, iteration: int) -> None:
        return None

    def _fitness_for(self, program: Program) -> float:
        return float(get_fitness_score(program.metrics, list(self.feature_dimensions)))

    def fitness(self, program: Program) -> float:
        return self._fitness_for(program)

    def _sorted(self, programs: Sequence[Program]) -> tuple[Program, ...]:
        return tuple(
            sorted(programs, key=lambda program: (-self.fitness(program), program.id))
        )

    @staticmethod
    def _working_ids_for(
        programs: Mapping[str, Program],
        *,
        size: int,
        feature_dimensions: Sequence[str],
    ) -> list[str]:
        ranked = sorted(
            programs.values(),
            key=lambda program: (
                -float(
                    get_fitness_score(program.metrics, list(feature_dimensions))
                ),
                program.id,
            ),
        )
        seen_fitnesses: set[float] = set()
        selected: list[str] = []
        for program in ranked:
            score = float(
                get_fitness_score(program.metrics, list(feature_dimensions))
            )
            if score in seen_fitnesses:
                continue
            seen_fitnesses.add(score)
            selected.append(program.id)
            if len(selected) == size:
                break
        return selected

    def _refresh_working_set(self) -> None:
        self._working_ids = self._working_ids_for(
            self._programs,
            size=self.working_set_size,
            feature_dimensions=self.feature_dimensions,
        )

    def working_programs(self) -> Sequence[Program]:
        return tuple(self._programs[program_id] for program_id in self._working_ids)

    def _programs_for_scope(self, scope: Any) -> tuple[Program, ...]:
        if scope is None:
            return tuple(self._programs[key] for key in sorted(self._programs))
        if scope == self.trunk_scope:
            return () if self._trunk_id is None else (self._programs[self._trunk_id],)
        return tuple(
            self._programs[key]
            for key in sorted(self._programs)
            if self._branches[key] == scope
        )

    def population(self, scope: Any = None) -> Sequence[Program]:
        return self._programs_for_scope(scope)

    def top_programs(self, n: int, scope: Any = None) -> Sequence[Program]:
        if n <= 0:
            return ()
        candidates = (
            self.working_programs()
            if scope is None
            else self._programs_for_scope(scope)
        )
        return self._sorted(candidates)[:n]

    def elites(self, scope: Any = None) -> Sequence[Program]:
        return self.top_programs(10, scope)

    def best_program(self) -> Optional[Program]:
        ranked = self.top_programs(1)
        return ranked[0] if ranked else None

    def all_fitnesses(self) -> Sequence[float]:
        return tuple(self.fitness(program) for program in self.population())

    def regions(self) -> Sequence[RegionSummary]:
        if self._trunk_id is None:
            return ()
        scopes = [self.trunk_scope]
        scopes.extend(
            f"branch:{child_id}"
            for child_id in sorted(self._children[self._trunk_id])
        )
        summaries = []
        for scope in scopes:
            programs = self._programs_for_scope(scope)
            summaries.append(
                RegionSummary(
                    scope=scope,
                    label="trunk" if scope == self.trunk_scope else str(scope),
                    best_fitness=max(
                        (self.fitness(program) for program in programs),
                        default=0.0,
                    ),
                    size=len(programs),
                )
            )
        return tuple(summaries)

    def per_scope_bests(self) -> Sequence[float]:
        return tuple(region.best_fitness for region in self.regions())

    def view(self, program: Program) -> ProgramView:
        return ProgramView.from_program(program, list(self.feature_dimensions))

    def views(self, programs: Sequence[Program]) -> Sequence[ProgramView]:
        return tuple(self.view(program) for program in programs)

    def snapshot(
        self, scope: Any = None, limit: Optional[int] = None
    ) -> PopulationSnapshot:
        if limit is not None:
            count = limit
        elif scope is None:
            count = self.working_set_size
        else:
            count = len(self._programs_for_scope(scope))
        programs = self.top_programs(count, scope)
        views = self.views(programs)
        return PopulationSnapshot(
            scope=scope,
            top_programs=tuple(views),
            fitnesses=tuple(
                self.fitness(program) for program in self._programs_for_scope(scope)
            ),
            best_program=views[0] if views else None,
            topology=self.topology,
            regions=tuple(self.regions()) if scope is None else (),
        )

    def add(
        self,
        program: Program,
        iteration: Optional[int] = None,
        target_scope: Any = None,
    ) -> str:
        if not isinstance(program, Program):
            raise TypeError("TreeStore accepts openevolve.database.Program instances only")
        if not program.id:
            raise ValueError("TreeStore program IDs must be non-empty")
        if program.id in self._programs:
            raise ValueError(f"duplicate TreeStore program ID: {program.id}")

        parent_id = program.parent_id
        if not self._programs:
            if parent_id is not None:
                raise ValueError("the first TreeStore program must be the parentless seed")
            branch = f"trunk:{program.id}"
        else:
            if parent_id is None:
                raise ValueError("TreeStore permits exactly one parentless seed")
            if parent_id == program.id:
                raise ValueError("TreeStore programs cannot parent themselves")
            if parent_id not in self._programs:
                raise ValueError(f"TreeStore parent is missing: {parent_id}")
            branch = (
                f"branch:{program.id}"
                if parent_id == self._trunk_id
                else self._branches[parent_id]
            )

        self._programs[program.id] = program
        self._parents[program.id] = parent_id
        self._children[program.id] = []
        self._branches[program.id] = branch
        if parent_id is None:
            self._trunk_id = program.id
        else:
            self._children[parent_id].append(program.id)
        if iteration is not None:
            self.last_iteration = int(iteration)
        self._refresh_working_set()
        return program.id

    def store_artifacts(
        self, program_id: str, artifacts: Mapping[str, Any]
    ) -> None:
        if program_id not in self._programs:
            raise ValueError(f"cannot store artifacts for unknown program: {program_id}")
        self._artifacts[program_id] = dict(artifacts)

    def native_select(self, target_scope: Any, num_inspirations: int) -> Selection:
        raise RuntimeError(
            "TreeStore has no native selection; compose it with a selection policy"
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
        if not isinstance(state, Mapping):
            raise ValueError("serialized Program must be a mapping")
        unknown = set(state) - set(_PROGRAM_FIELDS)
        missing = set(_PROGRAM_FIELDS) - set(state)
        if unknown or missing:
            raise ValueError(
                "invalid serialized Program fields: "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
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
            "last_iteration": self.last_iteration,
            "steps_per_generation": self.steps_per_generation,
            "working_set_size": self.working_set_size,
            "working_set_ids": list(self._working_ids),
            "feature_dimensions": list(self.feature_dimensions),
            "programs": {
                key: self._program_state(self._programs[key])
                for key in sorted(self._programs)
            },
            "parents": dict(sorted(self._parents.items())),
            "children": {
                key: sorted(self._children[key]) for key in sorted(self._children)
            },
            "branches": dict(sorted(self._branches.items())),
            "artifacts": _json_encode(
                {key: self._artifacts[key] for key in sorted(self._artifacts)}
            ),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise ValueError("TreeStore state must be a mapping")
        if state.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError(
                f"unsupported TreeStore schema version: {state.get('schema_version')!r}"
            )
        try:
            steps_per_generation = int(state["steps_per_generation"])
            working_set_size = int(state["working_set_size"])
            last_iteration = int(state["last_iteration"])
            feature_dimensions = tuple(state["feature_dimensions"])
            raw_programs = state["programs"]
            raw_parents = state["parents"]
            raw_children = state["children"]
            raw_branches = state["branches"]
            artifacts = _json_decode(state["artifacts"])
            persisted_working_ids = list(state["working_set_ids"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid TreeStore state shape") from exc
        if steps_per_generation <= 0 or working_set_size <= 0:
            raise ValueError("TreeStore state requires positive cadence and working-set size")
        if not all(isinstance(name, str) for name in feature_dimensions):
            raise ValueError("TreeStore feature dimensions must be strings")
        if not all(
            isinstance(value, Mapping)
            for value in (raw_programs, raw_parents, raw_children, raw_branches)
        ) or not isinstance(artifacts, Mapping):
            raise ValueError("invalid TreeStore programs, lineage, or artifacts state")
        if any(not isinstance(value, Mapping) for value in artifacts.values()):
            raise ValueError("TreeStore artifacts must be mappings by program ID")

        programs = {
            str(key): self._program_from_state(value)
            for key, value in raw_programs.items()
        }
        parents = {str(key): value for key, value in raw_parents.items()}
        children: Dict[str, list[str]] = {}
        for key, value in raw_children.items():
            if not isinstance(value, list) or not all(
                isinstance(child, str) for child in value
            ):
                raise ValueError("TreeStore child lists must contain string IDs")
            if len(set(value)) != len(value):
                raise ValueError("TreeStore child lists cannot contain duplicates")
            children[str(key)] = list(value)
        branches = {str(key): value for key, value in raw_branches.items()}
        program_ids = set(programs)

        if any(program.id != key for key, program in programs.items()):
            raise ValueError("serialized Program ID does not match its state key")
        if set(parents) != program_ids or set(children) != program_ids:
            raise ValueError("TreeStore lineage maps must cover exactly the programs")
        if set(branches) != program_ids:
            raise ValueError("TreeStore branches must cover exactly the programs")
        if any(
            program.parent_id != parents[program_id]
            for program_id, program in programs.items()
        ):
            raise ValueError("serialized Program parent does not match lineage state")

        roots = [key for key, parent in parents.items() if parent is None]
        if len(roots) != (1 if program_ids else 0):
            raise ValueError("TreeStore state requires exactly one trunk when non-empty")
        trunk_id = roots[0] if roots else None
        expected_children = {key: [] for key in program_ids}
        for key, parent in parents.items():
            if parent is not None:
                if not isinstance(parent, str) or parent not in program_ids:
                    raise ValueError("TreeStore state has a missing parent")
                if parent == key:
                    raise ValueError("TreeStore state contains self-parenting")
                expected_children[parent].append(key)
        expected_children = {
            key: sorted(value) for key, value in expected_children.items()
        }
        normalized_children = {key: sorted(value) for key, value in children.items()}
        if normalized_children != expected_children:
            raise ValueError("TreeStore child links do not match parent links")

        expected_branches: Dict[str, str] = {}
        if trunk_id is not None:
            expected_branches[trunk_id] = f"trunk:{trunk_id}"
            pending = list(expected_children[trunk_id])
            while pending:
                key = pending.pop(0)
                parent = parents[key]
                expected_branches[key] = (
                    f"branch:{key}"
                    if parent == trunk_id
                    else expected_branches[parent]
                )
                pending.extend(expected_children[key])
        if set(expected_branches) != program_ids:
            raise ValueError("TreeStore state contains a cycle or disconnected node")
        if branches != expected_branches:
            raise ValueError("TreeStore state has an invalid branch assignment")
        if not set(artifacts).issubset(program_ids):
            raise ValueError("TreeStore artifacts refer to unknown programs")

        expected_working_ids = self._working_ids_for(
            programs,
            size=working_set_size,
            feature_dimensions=feature_dimensions,
        )
        if persisted_working_ids != expected_working_ids:
            raise ValueError("TreeStore working-set state is inconsistent with programs")

        self.steps_per_generation = steps_per_generation
        self.working_set_size = working_set_size
        self.feature_dimensions = feature_dimensions
        self.last_iteration = last_iteration
        self._programs = programs
        self._parents = parents
        self._children = expected_children
        self._branches = branches
        self._artifacts = {str(key): dict(value) for key, value in artifacts.items()}
        self._working_ids = expected_working_ids
        self._trunk_id = trunk_id

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
            raise ValueError(f"cannot load TreeStore checkpoint: {target}") from exc
        self.load_state_dict(state)
