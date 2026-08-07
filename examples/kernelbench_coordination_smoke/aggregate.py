"""fast_p aggregation across confirmed arm champions (spec §13-14).

Operates on `ArmRunSummary` — what `run_arm.py` + `confirm.py` (task 0104,
out of scope here) would have produced. This module only computes the
report from already-confirmed data; it never launches or evaluates anything.
Every test in this slice fabricates `ArmRunSummary` values directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from examples.kernelbench_coordination_smoke.worker_protocol import WorkerResult

# spec §14: strict '>' at each threshold, matching KernelBench.
FAST_P_THRESHOLDS = (0.0, 1.0, 1.5, 2.0)

# spec §13: flag confirmation drift above 5%.
DRIFT_FLAG_THRESHOLD = 0.05
# spec §13: KernelBench's excessive-speedup line; >2x is merely "suspicious".
EXCESSIVE_SPEEDUP_THRESHOLD = 10.0
SUSPICIOUS_SPEEDUP_THRESHOLD = 2.0

MANDATORY_DISCLAIMER = (
    "Single-problem pipeline smoke test (N=1) — no comparative inference."
)


class AggregationError(ValueError):
    """Aggregation was refused — invariant mismatch or malformed input."""


@dataclass(frozen=True)
class ArmRunSummary:
    """What one arm's run_arm.py + confirm.py output would summarize to.

    `invariant_fingerprint` carries every value that MUST be identical across
    all four arms (problem hash, KernelBench commit, model, sampling, operator
    menu, evaluator settings, seed, token cap, ...) — everything EXCEPT the
    coordination module and its outcome. Aggregation refuses to run if these
    disagree between arms (spec §14).
    """

    arm: str
    invariant_fingerprint: Dict[str, Any]
    confirmed: Optional[WorkerResult]  # None: never confirmed correct
    screening_speedup: Optional[float]
    attempted: int
    compiled: int
    correct: int
    tokens_to_first_compile: Optional[int]
    tokens_to_first_correct: Optional[int]
    tokens_to_parity: Optional[int]
    tokens_to_confirmed_parity: Optional[int]
    mutation_tokens: int
    coordination_tokens: int
    operator_pulls: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.attempted < 0 or self.compiled < 0 or self.correct < 0:
            raise AggregationError(f"arm {self.arm!r}: attempt/compile/correct counts must be >= 0")
        if self.compiled > self.attempted or self.correct > self.compiled:
            raise AggregationError(
                f"arm {self.arm!r}: counts must satisfy correct <= compiled <= attempted"
            )
        if self.mutation_tokens < 0 or self.coordination_tokens < 0:
            raise AggregationError(f"arm {self.arm!r}: token counts must be >= 0")


@dataclass(frozen=True)
class ArmReport:
    arm: str
    fast_p: Dict[float, bool]
    screening_speedup: Optional[float]
    confirmed_speedup: Optional[float]
    confirmation_drift: Optional[float]
    confirmation_drift_flagged: bool
    confirmation_failed: bool
    compile_rate: float
    correct_rate: float
    correct_per_10k_tokens: float
    tokens_to_first_compile: Optional[int]
    tokens_to_first_correct: Optional[int]
    tokens_to_parity: Optional[int]
    tokens_to_confirmed_parity: Optional[int]
    total_tokens: int
    mutation_tokens: int
    coordination_tokens: int
    operator_pulls: Dict[str, int]
    excessive_speedup_flag: bool
    suspicious_speedup_flag: bool


@dataclass(frozen=True)
class AggregateReport:
    disclaimer: str
    arms: Dict[str, ArmReport]


def fast_p_for(confirmed: Optional[WorkerResult]) -> Dict[float, bool]:
    """spec §14: fast_p(c) = I(confirmed_correct(c) and confirmed_speedup(c) > p).
    A failed/missing confirmation zeros every threshold (spec §13)."""
    if confirmed is None or not confirmed.correct:
        return {p: False for p in FAST_P_THRESHOLDS}
    return {p: (confirmed.speedup > p) for p in FAST_P_THRESHOLDS}


def _assert_invariants_match(summaries: Sequence[ArmRunSummary]) -> None:
    if not summaries:
        raise AggregationError("no arm run summaries to aggregate")
    reference_arm = summaries[0].arm
    reference = summaries[0].invariant_fingerprint
    for other in summaries[1:]:
        if other.invariant_fingerprint != reference:
            keys = set(reference) | set(other.invariant_fingerprint)
            diff_keys = sorted(
                k for k in keys if reference.get(k) != other.invariant_fingerprint.get(k)
            )
            raise AggregationError(
                f"invariant fingerprint mismatch between {reference_arm!r} and "
                f"{other.arm!r}: differing keys {diff_keys}"
            )


def aggregate(summaries: Sequence[ArmRunSummary]) -> AggregateReport:
    """Refuses (raises AggregationError) on empty input or any invariant
    mismatch between arms — never a partial/best-effort report (spec §14)."""
    _assert_invariants_match(summaries)

    arms: Dict[str, ArmReport] = {}
    for s in summaries:
        confirmed = s.confirmed if (s.confirmed is not None and s.confirmed.correct) else None
        confirmation_failed = s.confirmed is not None and not s.confirmed.correct

        drift = None
        drift_flagged = False
        if confirmed is not None and s.screening_speedup:
            drift = abs(confirmed.speedup - s.screening_speedup) / s.screening_speedup
            drift_flagged = drift > DRIFT_FLAG_THRESHOLD

        total_tokens = s.mutation_tokens + s.coordination_tokens
        arms[s.arm] = ArmReport(
            arm=s.arm,
            fast_p=fast_p_for(confirmed),
            screening_speedup=s.screening_speedup,
            confirmed_speedup=confirmed.speedup if confirmed else None,
            confirmation_drift=drift,
            confirmation_drift_flagged=drift_flagged,
            confirmation_failed=confirmation_failed,
            compile_rate=(s.compiled / s.attempted) if s.attempted else 0.0,
            correct_rate=(s.correct / s.attempted) if s.attempted else 0.0,
            correct_per_10k_tokens=(s.correct / total_tokens * 10000) if total_tokens else 0.0,
            tokens_to_first_compile=s.tokens_to_first_compile,
            tokens_to_first_correct=s.tokens_to_first_correct,
            tokens_to_parity=s.tokens_to_parity,
            tokens_to_confirmed_parity=s.tokens_to_confirmed_parity,
            total_tokens=total_tokens,
            mutation_tokens=s.mutation_tokens,
            coordination_tokens=s.coordination_tokens,
            operator_pulls=dict(s.operator_pulls),
            excessive_speedup_flag=bool(confirmed and confirmed.speedup > EXCESSIVE_SPEEDUP_THRESHOLD),
            suspicious_speedup_flag=bool(confirmed and confirmed.speedup > SUSPICIOUS_SPEEDUP_THRESHOLD),
        )
    return AggregateReport(disclaimer=MANDATORY_DISCLAIMER, arms=arms)
