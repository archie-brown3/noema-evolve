"""Worker protocol + pluggable executor tests (task 0112, spec §16 items 4-10)."""

import dataclasses
import json
import unittest

from examples.kernelbench_coordination_smoke.executor import KernelExecutor, StubExecutor
from examples.kernelbench_coordination_smoke.worker_protocol import (
    ALLOWED_STATUSES,
    MAX_OUTPUT_BYTES,
    WorkerProtocolError,
    WorkerResult,
    make_correct_result,
    make_failed_result,
    make_timing,
    parse_worker_output,
)


class TestWorkerResultSchema(unittest.TestCase):
    def test_correct_result_is_valid(self):
        r = make_correct_result("abc", speedup=1.5)
        self.assertEqual(r.status, "correct")
        self.assertTrue(r.compiled and r.correct)

    def test_wrong_schema_version_rejected(self):
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict({**dataclasses.asdict(make_correct_result("x")), "schema_version": 2})

    def test_unknown_status_rejected(self):
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict({**dataclasses.asdict(make_correct_result("x")), "status": "bogus"})

    def test_unknown_field_rejected(self):
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict({**dataclasses.asdict(make_correct_result("x")), "extra_field": 1})

    def test_missing_fingerprint_rejected(self):
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict({**dataclasses.asdict(make_correct_result("x")), "fingerprint": {}})

    def test_correct_status_requires_compiled_and_correct_true(self):
        d = dataclasses.asdict(make_correct_result("x"))
        d["compiled"] = False
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict(d)

    def test_correct_status_requires_timing_blocks(self):
        d = dataclasses.asdict(make_correct_result("x"))
        d["candidate_timing"] = None
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict(d)

    def test_non_finite_or_negative_speedup_rejected(self):
        d = dataclasses.asdict(make_correct_result("x"))
        for bad in (float("nan"), float("inf"), -1.0, 0.0):
            d2 = dict(d)
            d2["speedup"] = bad
            with self.assertRaises(WorkerProtocolError):
                WorkerResult.from_dict(d2)

    def test_non_correct_status_cannot_claim_correct_true(self):
        with self.assertRaises(WorkerProtocolError):
            WorkerResult.from_dict(
                {**dataclasses.asdict(make_failed_result("x", "wrong_answer")), "correct": True}
            )

    def test_non_finite_timing_rejected(self):
        with self.assertRaises(WorkerProtocolError):
            make_timing(float("nan"))
        with self.assertRaises(WorkerProtocolError):
            make_timing(-1.0)

    def test_all_failure_statuses_construct_via_helper(self):
        for status in ALLOWED_STATUSES - {"correct"}:
            r = make_failed_result("x", status)
            self.assertEqual(r.status, status)
            self.assertFalse(r.correct)

    def test_make_failed_result_rejects_correct_status(self):
        with self.assertRaises(ValueError):
            make_failed_result("x", "correct")


class TestParseWorkerOutput(unittest.TestCase):
    def _raw(self, result: WorkerResult) -> str:
        return json.dumps(dataclasses.asdict(result))

    def test_valid_single_object_round_trips(self):
        r = make_correct_result("x", speedup=2.0)
        parsed = parse_worker_output(self._raw(r))
        self.assertEqual(parsed.speedup, 2.0)

    def test_not_json_rejected(self):
        with self.assertRaises(WorkerProtocolError):
            parse_worker_output("not json at all")

    def test_more_than_one_json_object_rejected(self):
        raw = self._raw(make_correct_result("x"))
        with self.assertRaises(WorkerProtocolError):
            parse_worker_output(raw + raw)

    def test_oversized_output_rejected(self):
        huge = "x" * (MAX_OUTPUT_BYTES + 1)
        with self.assertRaises(WorkerProtocolError):
            parse_worker_output(huge)

    def test_trailing_garbage_after_valid_json_rejected(self):
        raw = self._raw(make_correct_result("x")) + " garbage"
        with self.assertRaises(WorkerProtocolError):
            parse_worker_output(raw)


class TestStubExecutor(unittest.TestCase):
    def test_satisfies_kernel_executor_protocol(self):
        self.assertIsInstance(StubExecutor(), KernelExecutor)

    def test_unscripted_call_returns_default_correct_result(self):
        ex = StubExecutor(default_speedup=1.25)
        result = ex.execute("some code", "hash1")
        self.assertEqual(result.status, "correct")
        self.assertEqual(result.speedup, 1.25)
        self.assertEqual(ex.calls, ["hash1"])

    def test_scripted_result_is_returned_verbatim(self):
        scripted_result = make_failed_result("hash2", "timeout")
        ex = StubExecutor(scripted={"hash2": scripted_result})
        result = ex.execute("code", "hash2")
        self.assertIs(result, scripted_result)

    def test_never_touches_gpu_docker_or_network(self):
        import inspect

        source = inspect.getsource(StubExecutor)
        for forbidden in ("docker", "subprocess", "socket", "cuda", "torch", "requests", "urllib"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
