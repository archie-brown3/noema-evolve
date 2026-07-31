import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "experiments/0132/normalize.py"
SPEC = importlib.util.spec_from_file_location("normalize_0132", MODULE_PATH)
normalize = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(normalize)


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def test_stock_reconciliation_accepts_out_of_order_completion(tmp_path):
    write_jsonl(
        tmp_path / "attempt_trace.jsonl",
        [
            {
                "submission_index": 1,
                "completion_index": 0,
                "completed_mutation_request": True,
                "prompt": {"system": "s", "user": "b"},
            },
            {
                "submission_index": 0,
                "completion_index": 1,
                "completed_mutation_request": True,
                "prompt": {"system": "s", "user": "a"},
            },
        ],
    )
    rows = normalize.normalize_stock(tmp_path, seed=42)
    normalize.reconcile(rows, 2)
    assert {row["mutation_index"] for row in rows} == {0, 1}
    assert {row["seed"] for row in rows} == {42}


def test_reconciliation_rejects_missing_terminal_request():
    with pytest.raises(SystemExit, match="expected 2"):
        normalize.reconcile(
            [{"mutation_index": 0, "completed_mutation_request": True}], 2
        )


def test_noema_merges_admission_and_usage(tmp_path):
    write_jsonl(
        tmp_path / "attempt_trace.jsonl",
        [
            {
                "run_id": "r",
                "iteration": 0,
                "attempt": 0,
                "outcome": "accepted",
                "seed": 42,
                "target_scope": 0,
                "source_scope": 0,
                "parent": {"id": "initial"},
                "prompt": {"system": "s", "user": "u"},
                "response": "code",
                "evaluation": {"metrics": {"combined_score": 0.96}},
            }
        ],
    )
    write_jsonl(
        tmp_path / "selection_trace.jsonl",
        [
            {
                "iteration": 0,
                "admission": "replaced",
                "removed_program_ids": ["old"],
            }
        ],
    )
    write_jsonl(
        tmp_path / "llm_calls.jsonl",
        [
            {
                "iteration": 0,
                "account": "mutation",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "attempts": 1,
            }
        ],
    )
    rows = normalize.normalize_noema(tmp_path)
    assert rows[0]["admission"] == "replaced"
    assert rows[0]["total_tokens"] == 15
