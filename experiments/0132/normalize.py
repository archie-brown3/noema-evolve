#!/usr/bin/env python3
"""Normalize task-0132 attempt traces and enforce reconciliation invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def prompt_hash(prompt: dict | None) -> str | None:
    if not prompt:
        return None
    payload = json.dumps(prompt, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize_stock(run_dir: Path, seed: int) -> list[dict]:
    attempts = read_jsonl(run_dir / "attempt_trace.jsonl")
    usage_by_mutation: dict[int, list[dict]] = {}
    for record in read_jsonl(run_dir / "llm_calls.jsonl"):
        index = record.get("mutation_index")
        if index is not None:
            usage_by_mutation.setdefault(int(index), []).append(record)
    rows = []
    for attempt in attempts:
        index = int(attempt["submission_index"])
        usage = usage_by_mutation.get(index, [])
        rows.append(
            {
                **attempt,
                "seed": seed,
                "mutation_index": index,
                "prompt_hash": attempt.get("prompt_hash")
                or prompt_hash(attempt.get("prompt")),
                "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in usage),
                "completion_tokens": sum(
                    r.get("completion_tokens", 0) for r in usage
                ),
                "total_tokens": sum(r.get("total_tokens", 0) for r in usage),
                "cost": sum(r.get("cost", 0.0) for r in usage),
                "api_attempts": len(usage),
            }
        )
    return rows


def normalize_noema(run_dir: Path) -> list[dict]:
    attempts = read_jsonl(run_dir / "attempt_trace.jsonl")
    selections = {
        int(record["iteration"]): record
        for record in read_jsonl(run_dir / "selection_trace.jsonl")
    }
    calls = read_jsonl(run_dir / "llm_calls.jsonl")
    calls_by_iteration: dict[int, list[dict]] = {}
    for call in calls:
        if call.get("account") == "mutation":
            calls_by_iteration.setdefault(int(call["iteration"]), []).append(call)
    rows = []
    for attempt in attempts:
        if int(attempt.get("attempt", 0)) != 0:
            raise SystemExit("0132 noema normalization requires retries off")
        index = int(attempt["iteration"])
        selection = selections.get(index, {})
        calls_for_iteration = calls_by_iteration.get(index, [])
        outcome = attempt["outcome"]
        evaluation = attempt.get("evaluation") or {}
        parse_status = (
            "not_reached"
            if outcome in {"provider_failure", "budget_exhausted"}
            else ("failed" if outcome == "unparseable_response" else "succeeded")
        )
        if outcome == "immutable_boundary_violation":
            boundary = "failed"
        elif outcome in {"unparseable_response", "provider_failure", "budget_exhausted"}:
            boundary = "not_reached"
        else:
            boundary = "passed"
        rows.append(
            {
                "schema_version": 1,
                "system": "noema",
                "run_id": attempt.get("run_id"),
                "seed": attempt.get("seed"),
                "mutation_index": index,
                "submission_index": index,
                "completion_index": index,
                "completed_mutation_request": True,
                "parent_id": (attempt.get("parent") or {}).get("id"),
                "target_island": attempt.get("target_scope"),
                "source_island": attempt.get("source_scope"),
                "prompt_hash": prompt_hash(attempt.get("prompt")),
                "prompt": attempt.get("prompt"),
                "response": attempt.get("response"),
                "response_status": (
                    "received" if attempt.get("response") is not None else "failed"
                ),
                "parse_status": (
                    parse_status
                ),
                "boundary_status": boundary,
                "evaluator_status": (
                    "failed"
                    if outcome == "evaluation_failure"
                    else ("not_reached" if not evaluation else "succeeded")
                ),
                "metrics": evaluation.get("metrics"),
                "admission": selection.get("admission", "not_reached"),
                "removed_program_ids": selection.get("removed_program_ids", []),
                "outcome": outcome,
                "error": attempt.get("error"),
                "prompt_tokens": sum(
                    call.get("prompt_tokens", 0) for call in calls_for_iteration
                ),
                "completion_tokens": sum(
                    call.get("completion_tokens", 0) for call in calls_for_iteration
                ),
                "total_tokens": sum(
                    call.get("prompt_tokens", 0) + call.get("completion_tokens", 0)
                    for call in calls_for_iteration
                ),
                "cost": sum(call.get("cost", 0.0) for call in calls_for_iteration),
                "api_attempts": sum(
                    call.get("attempts", 1) for call in calls_for_iteration
                ),
            }
        )
    return rows


def reconcile(rows: list[dict], expected: int) -> None:
    indices = [int(row["mutation_index"]) for row in rows]
    if len(rows) != expected:
        raise SystemExit(f"expected {expected} terminal rows, found {len(rows)}")
    if len(set(indices)) != expected:
        raise SystemExit("mutation indices are not unique")
    if set(indices) != set(range(expected)):
        raise SystemExit("mutation indices are not exactly 0..expected-1")
    if not all(row.get("completed_mutation_request") for row in rows):
        raise SystemExit("one or more rows is not terminal")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", choices=["stock", "noema"], required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected", type=int, default=200)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    if args.system == "stock" and args.seed is None:
        parser.error("--seed is required for stock normalization")
    rows = (
        normalize_stock(args.run_dir, args.seed)
        if args.system == "stock"
        else normalize_noema(args.run_dir)
    )
    reconcile(rows, args.expected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda item: item["mutation_index"]):
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    print(f"wrote {len(rows)} normalized rows to {args.output}")


if __name__ == "__main__":
    main()
