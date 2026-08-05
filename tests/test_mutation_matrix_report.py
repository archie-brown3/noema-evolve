"""0188 Stage 5 — the matrix reducer must reject a truncated mutation log.

A mutant run that times out leaves the tests it never reached unrecorded. The
reducer used to read those as passing, so a partial log could still publish a
clean Rule 1 / Rule 2 verdict. It must refuse instead.
"""

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "mutation_matrix_report", ROOT / "scripts" / "mutation_matrix_report.py"
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def write_runs(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def row(mutant, nodeid, outcome="passed"):
    return {"mutant": mutant, "nodeid": nodeid, "when": "call", "outcome": outcome}


NODES = ["tests/test_a.py::test_one", "tests/test_a.py::test_two", "tests/test_a.py::test_three"]


def test_truncated_mutant_run_is_rejected(tmp_path):
    runs = tmp_path / "runs.jsonl"
    write_runs(
        runs,
        [row("none", n) for n in NODES]
        # the mutant run died after the first test — two nodes never scored
        + [row("m.one", NODES[0], "failed")],
    )
    outcomes = report.load(runs)
    rows = sorted(n for n, ok in outcomes["none"].items() if ok)

    with pytest.raises(SystemExit) as excinfo:
        report.reject_incomplete(outcomes, ["m.one"], rows)

    message = str(excinfo.value)
    assert "m.one" in message
    assert "2 missing" in message


def test_complete_run_is_accepted(tmp_path):
    runs = tmp_path / "runs.jsonl"
    write_runs(
        runs,
        [row("none", n) for n in NODES]
        + [row("m.one", NODES[0], "failed")]
        + [row("m.one", n) for n in NODES[1:]],
    )
    outcomes = report.load(runs)
    rows = sorted(n for n, ok in outcomes["none"].items() if ok)

    report.reject_incomplete(outcomes, ["m.one"], rows)  # no SystemExit
