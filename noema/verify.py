"""
Equal-token verification (task 0106).

A run charging an unmetered or partially-metered provider response as zero
tokens (`CallRecord.estimated=True`, see noema/budget/llm.py) cannot support an
equal-token comparison, even if every other call is metered exactly. This
module is the smallest policy that keeps that guarantee honest: any run
containing an unreconciled estimated call fails verification, full stop. No
reconciliation path exists yet (v1) — an unreconciled estimated call always
fails; there is no bound-substitution or provider-billing-lookback fallback.
Building that is separate future scope if it's ever actually needed.
"""

import json
from typing import Iterable, List

from noema.budget.ledger import CallRecord


class UnmeteredUsage(Exception):
    """Raised when a run contains a call whose token usage was not exactly
    server-reported (CallRecord.estimated=True) — the run cannot be presented
    as an equal-token result."""

    def __init__(self, offending: List[CallRecord]):
        self.offending = offending
        tags = sorted({r.tag for r in offending})
        super().__init__(
            f"{len(offending)} call(s) have unreconciled estimated usage "
            f"(tags: {tags}) — this run is not a valid equal-token result. "
            "Do not cite its token totals until reconciled."
        )


def verify_equal_token_metering(records: Iterable[CallRecord]) -> None:
    """Raise UnmeteredUsage if any record's usage was not exactly server-
    reported. Never estimates on your behalf (task 0055's rule extended to the
    run level, not just the individual call)."""
    offending = [r for r in records if r.estimated]
    if offending:
        raise UnmeteredUsage(offending)


def verify_equal_token_metering_from_jsonl(path: str) -> None:
    """Convenience: verify a completed run's llm_calls.jsonl directly, without
    needing a live TokenLedger. This is the artifact a human/reviewer actually
    has after a run finishes."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            records.append(CallRecord(**row))
    verify_equal_token_metering(records)
