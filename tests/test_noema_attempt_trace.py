import json
import os
import subprocess
import tempfile
import unittest

from noema.trace import AttemptTraceWriter, git_provenance


class TestAttemptTraceWriter(unittest.TestCase):
    def test_writes_one_complete_json_record_and_flushes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "attempt_trace.jsonl")
            writer = AttemptTraceWriter(
                path,
                run_id="run-1",
                config_sha256="abc",
                git_provenance={"revision": "deadbeef", "dirty": False},
            )

            writer.write(
                iteration=4,
                attempt=1,
                generation=2,
                arm="hifo",
                substrate="islands",
                seed=7,
                target_scope=0,
                source_scope=1,
                parent={"id": "p", "code": "def f(): pass"},
                inspirations=[],
                selection={"requested": {}, "honored": {}, "ignored": {}},
                operator={"name": "legacy"},
                coordination={
                    "system_block": "system advice",
                    "prompt_block": "user advice",
                    "attribution": {"insight": "x"},
                    "mode": "injected",
                },
                prompt={"system": "rendered system", "user": "rendered user"},
                response="full model response",
                candidate={"id": "it000004", "code": "def f(): return 2"},
                evaluation={"metrics": {"score": 2}, "artifacts": {}},
                outcome="accepted",
                error=None,
                ledger_call_ids=["call-000004"],
            )

            with open(path) as f:
                records = [json.loads(line) for line in f]

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["attempt_id"], "run-1:000004:01")
        self.assertEqual(record["prompt"]["user"], "rendered user")
        self.assertEqual(record["response"], "full model response")
        self.assertEqual(record["coordination"]["prompt_block"], "user advice")
        self.assertEqual(record["ledger_call_ids"], ["call-000004"])

    def test_rejects_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = AttemptTraceWriter(os.path.join(tmp, "attempt_trace.jsonl"))
            with self.assertRaises(ValueError):
                writer.write(iteration=0, attempt=0, outcome="mystery")

    def test_selection_record_captures_population_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "attempt_trace.jsonl")
            writer = AttemptTraceWriter(path, run_id="run-1")
            writer.write_selection(
                iteration=4,
                program_id="child",
                selected_attempt_id="run-1:000004:00",
                status="accepted",
                admission="replaced",
                target_scope=2,
                removed_program_ids=["old"],
            )
            with open(os.path.join(tmp, "selection_trace.jsonl")) as f:
                record = json.loads(f.readline())

        self.assertEqual(record["admission"], "replaced")
        self.assertEqual(record["target_scope"], 2)
        self.assertEqual(record["removed_program_ids"], ["old"])

    def test_binary_artifacts_are_reversibly_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "attempt_trace.jsonl")
            writer = AttemptTraceWriter(path)
            writer.write(
                iteration=0,
                attempt=0,
                outcome="evaluation_failure",
                evaluation={"artifacts": {"coverage": b"\x00\xff"}},
            )
            with open(path) as f:
                record = json.loads(f.readline())

        self.assertEqual(
            record["evaluation"]["artifacts"]["coverage"],
            {"__noema_bytes_base64__": "AP8="},
        )

    def test_dirty_digest_changes_when_dirty_contents_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp, check=True)
            path = os.path.join(tmp, "source.py")
            with open(path, "w") as f:
                f.write("value = 1\n")
            subprocess.run(["git", "add", "source.py"], cwd=tmp, check=True)
            subprocess.run(
                ["git", "commit", "-m", "base"], cwd=tmp, check=True, capture_output=True
            )

            with open(path, "w") as f:
                f.write("value = 2\n")
            first = git_provenance(tmp)
            with open(path, "w") as f:
                f.write("value = 3\n")
            second = git_provenance(tmp)

        self.assertNotEqual(first["dirty_sha256"], second["dirty_sha256"])
