"""Pluggable candidate-execution interface (task 0112).

The real worker executes untrusted generated CUDA in a disposable,
network-disabled GPU container — task 0104, explicitly NOT built here (this
sandbox has neither a GPU nor a container runtime, see the ticket's Why).
This module defines the interface that worker will implement, plus a
CPU-only, deterministic stub every test in this slice uses instead.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable

from examples.kernelbench_coordination_smoke.worker_protocol import (
    WorkerResult,
    make_correct_result,
)


@runtime_checkable
class KernelExecutor(Protocol):
    """What a candidate executor must provide. Silent on HOW — the real
    (docker-based) implementation and this CPU-only stub both satisfy it."""

    def execute(self, candidate_code: str, candidate_sha256: str) -> WorkerResult:
        """Run one candidate and return a validated WorkerResult.

        Must never raise on a CANDIDATE failure — a bad candidate is a
        WorkerResult status (compile_error, wrong_answer, timeout, ...), not
        a Python exception. Raising is reserved for executor-level faults.
        """
        ...


class StubExecutor:
    """CPU-only, deterministic, test-only. Returns a scripted WorkerResult
    per candidate hash, or a default 'correct' result for anything
    unscripted. Never touches a GPU, a container, or the network."""

    def __init__(
        self,
        scripted: Optional[Dict[str, WorkerResult]] = None,
        *,
        default_speedup: float = 1.0,
    ):
        self._scripted = dict(scripted or {})
        self._default_speedup = default_speedup
        self.calls: List[str] = []

    def execute(self, candidate_code: str, candidate_sha256: str) -> WorkerResult:
        self.calls.append(candidate_sha256)
        if candidate_sha256 in self._scripted:
            return self._scripted[candidate_sha256]
        return make_correct_result(candidate_sha256, speedup=self._default_speedup)
