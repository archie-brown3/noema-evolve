"""
Tests for noema.verify — the equal-token metering gate (task 0106).
"""

import unittest

from noema.budget.ledger import CallRecord
from noema.verify import UnmeteredUsage, verify_equal_token_metering


def record(estimated=False, tag="mutate", **overrides):
    fields = dict(
        account="mutation", tag=tag, model="m", prompt_tokens=10,
        completion_tokens=5, estimated=estimated,
    )
    fields.update(overrides)
    return CallRecord(**fields)


class TestVerifyEqualTokenMetering(unittest.TestCase):
    def test_fully_metered_run_passes_silently(self):
        records = [record(), record(), record(tag="pes.plan")]
        verify_equal_token_metering(records)  # must not raise

    def test_empty_run_passes(self):
        verify_equal_token_metering([])

    def test_one_estimated_call_fails_the_whole_run(self):
        records = [record(), record(estimated=True, tag="pes.reflect"), record()]
        with self.assertRaises(UnmeteredUsage) as cm:
            verify_equal_token_metering(records)
        self.assertEqual(len(cm.exception.offending), 1)
        self.assertEqual(cm.exception.offending[0].tag, "pes.reflect")

    def test_message_names_the_offending_tags(self):
        records = [record(estimated=True, tag="pes.reflect")]
        with self.assertRaises(UnmeteredUsage) as cm:
            verify_equal_token_metering(records)
        self.assertIn("pes.reflect", str(cm.exception))

    def test_multiple_estimated_calls_all_reported(self):
        records = [
            record(estimated=True, tag="mutate"),
            record(estimated=True, tag="pes.plan"),
            record(),
        ]
        with self.assertRaises(UnmeteredUsage) as cm:
            verify_equal_token_metering(records)
        self.assertEqual(len(cm.exception.offending), 2)


class TestVerifyFromJsonl(unittest.TestCase):
    def test_reads_llm_calls_jsonl_and_rejects_estimated_row(self):
        import json
        import os
        import tempfile

        from dataclasses import asdict

        from noema.verify import UnmeteredUsage, verify_equal_token_metering_from_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "llm_calls.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps(asdict(record())) + "\n")
                f.write(json.dumps(asdict(record(estimated=True, tag="hifo.extract"))) + "\n")

            with self.assertRaises(UnmeteredUsage) as cm:
                verify_equal_token_metering_from_jsonl(path)
            self.assertEqual(cm.exception.offending[0].tag, "hifo.extract")

    def test_reads_llm_calls_jsonl_passes_when_fully_metered(self):
        import json
        import os
        import tempfile

        from dataclasses import asdict

        from noema.verify import verify_equal_token_metering_from_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "llm_calls.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps(asdict(record())) + "\n")
                f.write(json.dumps(asdict(record(tag="pes.plan"))) + "\n")

            verify_equal_token_metering_from_jsonl(path)  # must not raise


if __name__ == "__main__":
    unittest.main()
