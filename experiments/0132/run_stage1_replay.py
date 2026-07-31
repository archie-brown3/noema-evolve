#!/usr/bin/env python3
"""Run and verify task-0132 Stage 1 without network or paid model calls."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = (
    "valid_improvement",
    "valid_regression",
    "no_diff",
    "malformed",
    "boundary_destroyed",
    "evaluation_failure",
    "provider_failure",
    "over_length",
)


def run_command(command: list[str], env: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def classify_noema(summary: dict) -> dict:
    attempt = summary["attempts"][-1]
    outcome = attempt["outcome"]
    selection = summary.get("selections", [])
    return {
        "response": "failed" if attempt.get("response") is None else "received",
        "parse": (
            "not_reached"
            if outcome in {"provider_failure", "budget_exhausted"}
            else ("failed" if outcome == "unparseable_response" else "succeeded")
        ),
        "boundary": (
            "not_reached"
            if outcome
            in {"provider_failure", "budget_exhausted", "unparseable_response"}
            else (
                "failed"
                if outcome == "immutable_boundary_violation"
                else "passed"
            )
        ),
        "evaluator": (
            "failed"
            if outcome == "evaluation_failure"
            else (
                "succeeded"
                if attempt.get("evaluation")
                else "not_reached"
            )
        ),
        "admission": selection[-1]["admission"] if selection else "not_reached",
    }


def classify_stock(summary: dict) -> dict:
    attempt = summary["attempts"][-1]
    return {
        "response": attempt["response_status"],
        "parse": attempt["parse_status"],
        "boundary": attempt["boundary_status"],
        "evaluator": attempt["evaluator_status"],
        "admission": attempt["admission"],
    }


def verify(report: dict) -> None:
    shared_expected = {
        "valid_improvement": ("received", "succeeded", "succeeded"),
        "valid_regression": ("received", "succeeded", "succeeded"),
        "no_diff": ("received", "succeeded", "succeeded"),
        "malformed": ("received", "failed", "not_reached"),
        "provider_failure": ("failed", "not_reached", "not_reached"),
        "over_length": ("received", "succeeded", "not_reached"),
    }
    for scenario, (response, parse, evaluator) in shared_expected.items():
        for system in ("noema", "stock"):
            actual = report["scenarios"][scenario][system]["classification"]
            assert actual["response"] == response, (scenario, system, actual)
            assert actual["parse"] == parse, (scenario, system, actual)
            assert actual["evaluator"] == evaluator, (scenario, system, actual)

    boundary = report["scenarios"]["boundary_destroyed"]
    assert boundary["noema"]["classification"]["boundary"] == "failed"
    assert boundary["stock"]["classification"]["boundary"] == "not_enforced"
    assert boundary["stock"]["classification"]["evaluator"] == "succeeded"

    evaluation = report["scenarios"]["evaluation_failure"]
    assert evaluation["noema"]["classification"]["evaluator"] == "failed"
    assert evaluation["noema"]["classification"]["admission"] == "not_reached"
    assert evaluation["stock"]["classification"]["evaluator"] == "failed"
    assert evaluation["stock"]["classification"]["admission"] in {
        "inserted",
        "replaced",
    }

    for scenario in SCENARIOS:
        for system in ("noema", "stock"):
            summary = report["scenarios"][scenario][system]["summary"]
            assert len(summary["attempts"]) == 1, (scenario, system)

    ordering = report["out_of_order"]["stock"]["summary"]["attempts"]
    assert len(ordering) == 4
    assert {row["submission_index"] for row in ordering} == {1, 2, 3, 4}
    assert [row["completion_index"] for row in ordering] == [0, 1, 2, 3]
    assert [row["submission_index"] for row in ordering] != [1, 2, 3, 4]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--work-root", type=Path, default=Path("/private/tmp/noema-0132-replay")
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "experiments/0132/stage1-replay-report.json",
    )
    args = parser.parse_args()
    if args.work_root.exists():
        shutil.rmtree(args.work_root)
    args.work_root.mkdir(parents=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    report = {"schema_version": 1, "network_calls": 0, "scenarios": {}}
    for scenario in SCENARIOS:
        report["scenarios"][scenario] = {}
        for system, python, script in (
            ("noema", ROOT / ".venv/bin/python", "replay_noema.py"),
            (
                "stock",
                ROOT / ".openevolve-stock/.venv/bin/python",
                "replay_stock.py",
            ),
        ):
            run_dir = args.work_root / scenario / system
            summary_path = run_dir / "summary.json"
            run_command(
                [
                    str(python),
                    str(Path(__file__).with_name(script)),
                    "--scenario",
                    scenario,
                    "--output-dir",
                    str(run_dir),
                    "--summary",
                    str(summary_path),
                ],
                env,
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            classification = (
                classify_noema(summary)
                if system == "noema"
                else classify_stock(summary)
            )
            report["scenarios"][scenario][system] = {
                "classification": classification,
                "summary": summary,
            }

    stock_order_dir = args.work_root / "out_of_order" / "stock"
    stock_order_summary = stock_order_dir / "summary.json"
    run_command(
        [
            str(ROOT / ".openevolve-stock/.venv/bin/python"),
            str(Path(__file__).with_name("replay_stock.py")),
            "--scenario",
            "out_of_order",
            "--output-dir",
            str(stock_order_dir),
            "--summary",
            str(stock_order_summary),
        ],
        env,
    )
    report["out_of_order"] = {
        "stock": {
            "summary": json.loads(stock_order_summary.read_text(encoding="utf-8"))
        }
    }
    verify(report)
    report["verified"] = True
    report["declared_native_differences"] = [
        "Stock does not enforce Noema's immutable EVOLVE-BLOCK boundary.",
        "Stock admits evaluator-error metric records; Noema rejects them.",
        "A provider exception terminates Noema's run after tracing the request; "
        "Stock records the failed request and continues its controller loop.",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"verified Stage 1 replay; wrote {args.report}")


if __name__ == "__main__":
    main()
