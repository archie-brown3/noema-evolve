"""Harness-side instrumentation for the 0132 off-by-one investigation.

Records every mutation of `ProgramDatabase.programs` — insert, overwrite,
delete, pop — with the calling stack, plus every `add()` call including the
initial program (which the trajectory recorder filters out because it has no
parent). Lives here so both trees stay untouched.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict, List


class EventLog:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def emit(self, kind: str, **fields: Any) -> None:
        self.events.append({"kind": kind, **fields})

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for event in self.events:
                fh.write(json.dumps(event) + "\n")


def _stack() -> List[str]:
    # Drop this helper and the dict-method frame; keep the interesting callers.
    frames = traceback.extract_stack()[:-2]
    return [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in frames[-8:]]


class TracedPrograms(dict):
    """A `dict` that logs every mutation of the population mapping."""

    def __init__(self, source: dict, log: EventLog) -> None:
        super().__init__(source)
        self._log = log

    def __setitem__(self, key: str, value: Any) -> None:
        self._log.emit(
            "programs.set",
            id=key,
            overwrite=key in self,
            size_before=len(self),
            stack=_stack(),
        )
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        self._log.emit("programs.del", id=key, size_before=len(self), stack=_stack())
        super().__delitem__(key)

    def pop(self, key, *default):  # type: ignore[override]
        self._log.emit("programs.pop", id=key, size_before=len(self), stack=_stack())
        return super().pop(key, *default)

    def clear(self) -> None:
        self._log.emit("programs.clear", size_before=len(self), stack=_stack())
        super().clear()


def install(database: Any, log: EventLog) -> Any:
    """Swap in a traced `programs` mapping. Returns the inner openevolve db."""
    inner = getattr(database, "_db", database)
    inner.programs = TracedPrograms(inner.programs, log)
    return inner


def describe(inner: Any, program: Any, code_id) -> Dict[str, Any]:
    feature_key = None
    try:
        coords = inner._calculate_feature_coords(program)
        feature_key = inner._feature_coords_to_key(coords)
    except Exception as exc:  # never let instrumentation break the run
        feature_key = f"<error: {type(exc).__name__}: {exc}>"
    return {
        "id": program.id,
        "code_id": code_id(program.code),
        "parent_id": getattr(program, "parent_id", None),
        "iteration_found": getattr(program, "iteration_found", None),
        "feature_key": feature_key,
        "metrics": {
            k: v
            for k, v in (program.metrics or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        },
    }


def snapshot_ids(inner: Any, code_id) -> Dict[str, Any]:
    return {
        "n_programs": len(inner.programs),
        "ids": [
            {"id": pid, "code_id": code_id(p.code), "parent_id": getattr(p, "parent_id", None)}
            for pid, p in inner.programs.items()
        ],
        "islands": [sorted(i) for i in getattr(inner, "islands", [])],
        "archive": sorted(getattr(inner, "archive", []) or []),
        "feature_map": dict(getattr(inner, "feature_map", {}) or {}),
        "best_program_id": getattr(inner, "best_program_id", None),
    }
