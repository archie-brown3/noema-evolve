"""Worker result schema, version 1 (spec/KERNELBENCH-P88-PILOT.md §10).

Pure Python — no CUDA, no GPU, no container. Defines and validates the JSON
contract the (future, out-of-scope, task 0104) sandboxed worker must emit,
so every other piece of this slice (aggregate.py, executor.py's stub) can be
built and tested against a real, enforced schema today.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

SCHEMA_VERSION = 1

# spec §10: allowed statuses.
ALLOWED_STATUSES = frozenset({
    "static_reject", "compile_error", "runtime_error", "wrong_answer",
    "timing_error", "timeout", "protocol_error", "correct",
})

MAX_OUTPUT_BYTES = 65536  # spec §10: "output above the size cap" is rejected


class WorkerProtocolError(ValueError):
    """A worker result (or raw worker output) violates the schema contract."""


@dataclass(frozen=True)
class TimingStats:
    unit: str
    warmups: int
    trials: int
    discard_first: int
    mean: float
    std: float
    min: float
    max: float

    def __post_init__(self) -> None:
        for name in ("mean", "std", "min", "max"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise WorkerProtocolError(f"timing.{name} must be finite, got {value!r}")
            if value < 0:
                raise WorkerProtocolError(f"timing.{name} must be non-negative, got {value!r}")
        if self.warmups < 0 or self.trials <= 0 or self.discard_first < 0:
            raise WorkerProtocolError("timing warmups/trials/discard_first out of range")


@dataclass(frozen=True)
class WorkerResult:
    """Schema version 1 (spec §10). `problem`/`correctness`/`metadata` are
    opaque-to-us JSON-safe mappings; only the fields this slice's aggregator
    and tests reason about are typed."""

    schema_version: int
    status: str
    candidate_sha256: str
    kernelbench_commit: str
    backend: str
    precision: str
    compiled: bool
    correct: bool
    speedup: float
    fingerprint: Dict[str, Any]
    problem: Dict[str, Any] = field(default_factory=dict)
    correctness: Dict[str, Any] = field(default_factory=dict)
    candidate_timing: Optional[TimingStats] = None
    reference_timing: Optional[TimingStats] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise WorkerProtocolError(
                f"unsupported schema_version {self.schema_version!r}, expected {SCHEMA_VERSION}"
            )
        if self.status not in ALLOWED_STATUSES:
            raise WorkerProtocolError(f"unknown status {self.status!r}")
        if not self.fingerprint:
            # spec §10: "missing fingerprints" is a rejection condition.
            raise WorkerProtocolError("fingerprint is required and cannot be empty")
        if self.status == "correct":
            if not (self.compiled and self.correct):
                raise WorkerProtocolError(
                    "status 'correct' requires compiled=True and correct=True"
                )
            if self.candidate_timing is None or self.reference_timing is None:
                raise WorkerProtocolError("status 'correct' requires both timing blocks")
            if not math.isfinite(self.speedup) or self.speedup <= 0:
                raise WorkerProtocolError(
                    f"speedup must be finite and positive, got {self.speedup!r}"
                )
        else:
            if self.correct:
                raise WorkerProtocolError(f"status {self.status!r} cannot have correct=True")
            if self.compiled and self.status == "static_reject":
                raise WorkerProtocolError("status 'static_reject' cannot have compiled=True")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkerResult":
        if not isinstance(data, dict):
            raise WorkerProtocolError("worker result must be a JSON object")
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise WorkerProtocolError(f"unknown field(s) in worker result: {sorted(unknown)}")
        kwargs = dict(data)
        for timing_field in ("candidate_timing", "reference_timing"):
            raw = kwargs.get(timing_field)
            if raw is not None:
                if not isinstance(raw, dict):
                    raise WorkerProtocolError(f"{timing_field} must be a JSON object")
                try:
                    kwargs[timing_field] = TimingStats(**raw)
                except TypeError as exc:
                    raise WorkerProtocolError(f"invalid {timing_field}: {exc}") from exc
        try:
            return cls(**kwargs)
        except TypeError as exc:
            raise WorkerProtocolError(f"invalid worker result shape: {exc}") from exc


def parse_worker_output(raw: str) -> WorkerResult:
    """Parse and validate raw worker stdout: exactly one bounded JSON object.

    spec §10: "Reject ... more than one JSON object" / "output above the size
    cap". Both are enforced here, before schema validation.
    """
    if len(raw.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise WorkerProtocolError(
            f"worker output exceeds the {MAX_OUTPUT_BYTES}-byte cap"
        )
    decoder = json.JSONDecoder()
    stripped = raw.strip()
    try:
        obj, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise WorkerProtocolError(f"worker output is not valid JSON: {exc}") from exc
    remainder = stripped[end:].strip()
    if remainder:
        raise WorkerProtocolError("worker output contains more than one JSON object")
    return WorkerResult.from_dict(obj)


# -- test/stub construction helpers ------------------------------------------

def make_timing(mean: float, *, std: float = 0.01, warmups: int = 5, trials: int = 100) -> TimingStats:
    return TimingStats(
        unit="ms", warmups=warmups, trials=trials, discard_first=1,
        mean=mean, std=std, min=max(mean - std, 0.0), max=mean + std,
    )


def _default_fingerprint() -> Dict[str, Any]:
    return {
        "gpu_name": "stub-gpu", "compute_capability": "0.0",
        "driver": "stub", "cuda_runtime": "stub", "torch": "stub", "python": "stub",
        "image_digest": "stub",
    }


def make_correct_result(
    candidate_sha256: str, *, speedup: float = 1.0, candidate_ms: float = 1.0,
    reference_ms: Optional[float] = None,
) -> WorkerResult:
    reference_ms = reference_ms if reference_ms is not None else candidate_ms * speedup
    return WorkerResult(
        schema_version=SCHEMA_VERSION, status="correct", candidate_sha256=candidate_sha256,
        kernelbench_commit="423217d9fda91e0c2d67e4a43bf62f96f6d104f1", backend="cuda",
        precision="fp32", compiled=True, correct=True, speedup=speedup,
        fingerprint=_default_fingerprint(),
        candidate_timing=make_timing(candidate_ms), reference_timing=make_timing(reference_ms),
    )


def make_failed_result(candidate_sha256: str, status: str) -> WorkerResult:
    if status not in ALLOWED_STATUSES or status == "correct":
        raise ValueError(f"make_failed_result status must be a non-'correct' status, got {status!r}")
    return WorkerResult(
        schema_version=SCHEMA_VERSION, status=status, candidate_sha256=candidate_sha256,
        kernelbench_commit="423217d9fda91e0c2d67e4a43bf62f96f6d104f1", backend="cuda",
        precision="fp32", compiled=(status not in ("static_reject",)), correct=False,
        speedup=0.0, fingerprint=_default_fingerprint(),
    )
