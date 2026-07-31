#!/usr/bin/env python3
"""Stage 4 analysis for task 0132: paired endpoints, trajectories, diagnostics, verdict.

Consumes the normalized JSONL produced by `normalize.py` (one file per
condition x seed) and applies the preregistered verdict rule frozen in
`preflight-manifest.json`. Nothing here touches the network or the raw run
directories; re-running it on the same inputs produces the same report.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

CHECKPOINTS = (50, 100, 150, 200)
# Best-fit initial program, frozen in Stage 0.
BASELINE_MEAN_EXCESS_BINS = 18.4

# Fields whose value distribution is compared across conditions as a process diagnostic.
DIAGNOSTIC_FIELDS = (
    "response_status",
    "parse_status",
    "boundary_status",
    "evaluator_status",
    "admission",
    "outcome",
)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def scored(row: dict) -> float | None:
    """mean_excess_bins for a row that actually produced a valid evaluation."""
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        return None
    value = metrics.get("mean_excess_bins")
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def best_so_far(rows: list[dict], baseline: float) -> list[float]:
    """best[k] = best mean_excess_bins after k+1 terminal requests (lower is better)."""
    curve: list[float] = []
    best = baseline
    for row in sorted(rows, key=lambda r: int(r["mutation_index"])):
        value = scored(row)
        if value is not None and value < best:
            best = value
        curve.append(best)
    return curve


def diagnostics(rows: list[dict]) -> dict:
    out: dict = {
        field: dict(Counter(str(row.get(field)) for row in rows))
        for field in DIAGNOSTIC_FIELDS
    }
    out["no_diff"] = sum(1 for row in rows if row.get("no_diff"))
    out["evaluated"] = sum(1 for row in rows if scored(row) is not None)
    for field in ("prompt_tokens", "completion_tokens", "total_tokens", "api_attempts"):
        out[field] = sum(row.get(field, 0) or 0 for row in rows)
    out["cost"] = sum(row.get("cost", 0.0) or 0.0 for row in rows)
    out["distinct_prompt_hashes"] = len(
        {row.get("prompt_hash") for row in rows if row.get("prompt_hash")}
    )
    return out


def summarize_run(rows: list[dict], baseline: float, expected: int) -> dict:
    curve = best_so_far(rows, baseline)
    return {
        "requests": len(rows),
        "complete": len(rows) == expected,
        "endpoint_mean_excess_bins": curve[-1] if curve else None,
        "trajectory": {
            str(k): (curve[k - 1] if len(curve) >= k else None) for k in CHECKPOINTS
        },
        "auc_best_so_far": sum(curve),
        "diagnostics": diagnostics(rows),
    }


def pair(left: dict, right: dict, margin: float) -> dict:
    """Paired per-seed difference (left - right) in endpoint mean_excess_bins."""
    seeds = sorted(set(left) & set(right))
    per_seed = {}
    for seed in seeds:
        a = left[seed]["endpoint_mean_excess_bins"]
        b = right[seed]["endpoint_mean_excess_bins"]
        per_seed[str(seed)] = {
            "left": a,
            "right": b,
            "difference": None if a is None or b is None else a - b,
            "trajectory_difference": {
                k: None
                if left[seed]["trajectory"][k] is None
                or right[seed]["trajectory"][k] is None
                else left[seed]["trajectory"][k] - right[seed]["trajectory"][k]
                for k in left[seed]["trajectory"]
            },
        }
    diffs = [v["difference"] for v in per_seed.values()]
    usable = [d for d in diffs if d is not None]
    return {
        "seeds_compared": seeds,
        "unpaired_seeds": sorted(set(left) ^ set(right)),
        "per_seed": per_seed,
        "max_abs_difference": max((abs(d) for d in usable), default=None),
        "within_margin": bool(usable)
        and len(usable) == len(diffs)
        and all(abs(d) <= margin for d in usable),
    }


def verdict(runs: dict, primary: dict, margin: float, expected: int) -> dict:
    """Preregistered rule. Criterion 3 (no unexplained systematic process
    discrepancy) is not automatable — it is surfaced for human adjudication."""
    incomplete = [
        f"{condition}:s{seed}"
        for condition, seeds in runs.items()
        for seed, summary in seeds.items()
        if not summary["complete"]
    ]
    if incomplete:
        return {
            "verdict": "inconclusive",
            "reason": f"runs did not reach {expected} terminal requests: {incomplete}",
            "process_review_required": True,
        }
    if primary["unpaired_seeds"] or len(primary["seeds_compared"]) < 5:
        return {
            "verdict": "inconclusive",
            "reason": (
                "fewer than five paired seeds available for the primary comparison: "
                f"paired={primary['seeds_compared']}, unpaired={primary['unpaired_seeds']}"
            ),
            "process_review_required": True,
        }
    if not primary["within_margin"]:
        return {
            "verdict": "fail",
            "reason": (
                f"paired endpoint difference {primary['max_abs_difference']} "
                f"exceeds the approved margin +/-{margin}"
            ),
            "process_review_required": True,
        }
    return {
        "verdict": "pass",
        "reason": (
            f"all five paired endpoints within +/-{margin} mean_excess_bins "
            f"(max |diff| = {primary['max_abs_difference']})"
        ),
        # ponytail: criterion 3 stays human-judged; the diagnostics tables below are the input.
        "process_review_required": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        nargs=3,
        metavar=("CONDITION", "SEED", "NORMALIZED_JSONL"),
        action="append",
        required=True,
    )
    parser.add_argument("--primary-left", default="noema-null")
    parser.add_argument("--primary-right", default="stock-native")
    parser.add_argument("--margin", type=float, default=0.2)
    parser.add_argument("--baseline", type=float, default=BASELINE_MEAN_EXCESS_BINS)
    parser.add_argument("--expected", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runs: dict[str, dict[int, dict]] = {}
    for condition, seed, path in args.run:
        rows = read_jsonl(Path(path))
        runs.setdefault(condition, {})[int(seed)] = summarize_run(
            rows, args.baseline, args.expected
        )

    conditions = sorted(runs)
    pairs = {
        f"{a}_vs_{b}": pair(runs[a], runs[b], args.margin)
        for i, a in enumerate(conditions)
        for b in conditions[i + 1 :]
    }
    primary_key = f"{args.primary_left}_vs_{args.primary_right}"
    if primary_key not in pairs:
        primary_key = f"{args.primary_right}_vs_{args.primary_left}"
    if primary_key not in pairs:
        raise SystemExit(
            f"primary comparison {args.primary_left} vs {args.primary_right} "
            f"not present in supplied conditions {conditions}"
        )

    report = {
        "margin_mean_excess_bins": args.margin,
        "baseline_mean_excess_bins": args.baseline,
        "expected_requests": args.expected,
        "checkpoints": list(CHECKPOINTS),
        "conditions": conditions,
        "runs": {c: {str(s): v for s, v in seeds.items()} for c, seeds in runs.items()},
        "pairwise": pairs,
        "primary_comparison": primary_key,
        **verdict(runs, pairs[primary_key], args.margin, args.expected),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"{report['verdict']}: {report['reason']}")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
