"""
Tests for the CoordinationModule interface and the NullCoordination (OFF) arm
"""

import asyncio
import json
import unittest

from noema.coordination.base import Advice, GenerationContext, NullCoordination
from noema.substrate.views import ProgramView


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
        self.assertIsNone(advice.sampling_hint)


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


class TestGenerationContext(unittest.TestCase):
    def test_context_is_frozen(self):
        ctx = make_ctx()
        with self.assertRaises(Exception):
            ctx.iteration = 99


if __name__ == "__main__":
    unittest.main()
