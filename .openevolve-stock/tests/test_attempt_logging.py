"""Tests for task-0132 attempt instrumentation."""

import json
from pathlib import Path

from openevolve.process_parallel import _append_attempt_record, _prompt_hash


def test_prompt_hash_is_canonical():
    left = {"system": "s", "user": "u"}
    right = {"user": "u", "system": "s"}
    assert _prompt_hash(left) == _prompt_hash(right)


def test_attempt_record_is_opt_in(monkeypatch, tmp_path):
    target = tmp_path / "attempts.jsonl"
    monkeypatch.delenv("OPENEVOLVE_ATTEMPT_LOG", raising=False)
    _append_attempt_record({"submission_index": 0})
    assert not target.exists()

    monkeypatch.setenv("OPENEVOLVE_ATTEMPT_LOG", str(target))
    _append_attempt_record({"submission_index": 1, "admission": "inserted"})
    record = json.loads(Path(target).read_text(encoding="utf-8"))
    assert record["submission_index"] == 1
    assert record["admission"] == "inserted"
