"""
Tests for the CoordinationModule interface and the NullCoordination (OFF) arm
"""

import asyncio
import json
import unittest

from noema.coordination.base import Advice, CoordinationModule, GenerationContext, NullCoordination
from noema.views import ProgramView


def make_ctx(**overrides) -> GenerationContext:
    defaults = dict(
        iteration=1,
        generation=0,
        island=0,
        parent=ProgramView(id="p", code="def f(): pass", fitness=0.5),
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


class TestAdvice(unittest.TestCase):
    def test_default_advice_is_noop(self):
        advice = Advice()
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.system_block, "")
        self.assertEqual(advice.attribution, {})
        self.assertFalse(hasattr(advice, "sampling_hint"))


class TestNullCoordination(unittest.TestCase):
    def test_advise_returns_noop_advice(self):
        module = NullCoordination()
        advice = asyncio.run(module.advise(make_ctx()))
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.attribution, {})

    def test_report_and_generation_end_are_noops(self):
        module = NullCoordination()
        module.report_result(make_ctx(), child=None, attribution={}, eval_failed=True)
        asyncio.run(module.on_generation_end(make_ctx()))

    def test_state_dict_round_trip_and_json_serializable(self):
        module = NullCoordination()
        state = module.state_dict()
        json.dumps(state)  # must be checkpointable
        module.load_state_dict(state)

    def test_log_snapshot_json_serializable(self):
        json.dumps(NullCoordination().log_snapshot())

    def test_retry_advice_default_returns_empty_string(self):
        # Non-abstract default: every module inherits this no-op without override
        module = NullCoordination()
        advice = asyncio.run(module.retry_advice(make_ctx(), "some error", 1))
        self.assertEqual(advice, "")

    def test_retry_advice_is_coroutine(self):
        import inspect
        module = NullCoordination()
        self.assertTrue(
            inspect.iscoroutinefunction(CoordinationModule.retry_advice),
            "retry_advice must be async (awaitable) on the base class",
        )

    def test_null_coordination_inherits_noop_without_override(self):
        # NullCoordination does NOT define retry_advice — it must inherit the base no-op
        self.assertFalse(
            "retry_advice" in NullCoordination.__dict__,
            "NullCoordination must not override retry_advice; it inherits the base no-op",
        )
        advice = asyncio.run(NullCoordination().retry_advice(make_ctx(), "err", 0))
        self.assertEqual(advice, "")


class TestGenerationContext(unittest.TestCase):
    def test_context_is_frozen(self):
        ctx = make_ctx()
        with self.assertRaises(Exception):
            ctx.iteration = 99


if __name__ == "__main__":
    unittest.main()
