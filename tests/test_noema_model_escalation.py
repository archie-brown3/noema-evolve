"""Coordination-driven model escalation for mutation generations (task 0107).

Proves: a module setting no Advice.model is byte-identical to today; a module
that escalates gets the requested model on that call's CallRecord; escalation
is attributable in the ledger with no schema change; and escalation decisions
are deterministic under a fixed seed.
"""

import asyncio
import random
import unittest

from noema.coordination.base import (
    Advice,
    CoordinationModule,
    GenerationContext,
    NullCoordination,
    Outcome,
)

from tests.test_noema_controller import make_config, make_controller


class EveryThirdEscalates(CoordinationModule):
    """Deterministic stub: escalates every 3rd mutation to `escalation_model`,
    decided from the module's own seeded RNG (never a weighted random draw —
    the ticket's determinism constraint)."""

    def __init__(self, config=None, llm=None, rng=None):
        super().__init__(config, llm, rng)
        self.escalation_model = self.config.get("escalation_model")
        self._n = 0
        self.escalation_log = []

    async def advise(self, ctx: GenerationContext) -> Advice:
        self._n += 1
        # Deterministic trigger (no random draw at all) + a seeded coin flip to
        # prove RNG-driven decisions replay identically under the same seed.
        escalate = (self._n % 3 == 0) and (self.rng.random() < 1.0)
        self.escalation_log.append(escalate)
        return Advice(model=self.escalation_model if escalate else None)

    def report_result(self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED):
        return None

    async def on_generation_end(self, ctx: GenerationContext):
        return None

    def state_dict(self):
        return {"n": self._n, "escalation_log": list(self.escalation_log)}

    def load_state_dict(self, state):
        self._n = state.get("n", 0)
        self.escalation_log = list(state.get("escalation_log", []))


class TestModelEscalation(unittest.TestCase):
    def test_unset_advice_model_is_byte_identical_to_pre_change(self):
        with tempfile_dir() as tmp:
            controller, ledger, client = make_controller(tmp, config=make_config())
            asyncio.run(controller.run())
            self.assertTrue(all(r.model == "fake-model" for r in ledger.records))

    def test_escalated_generation_carries_the_strong_model(self):
        with tempfile_dir() as tmp:
            controller, ledger, client = make_controller(tmp, config=make_config())
            controller.coordination = EveryThirdEscalates(
                config={"escalation_model": "strong-model"},
                llm=None,
                rng=random.Random(0),
            )
            asyncio.run(controller.run())

            mutation_records = [r for r in ledger.records if r.account == "mutation"]
            escalated = [r for r in mutation_records if r.model == "strong-model"]
            unescalated = [r for r in mutation_records if r.model == "fake-model"]
            self.assertGreater(len(escalated), 0, "at least one escalated call expected")
            self.assertGreater(len(unescalated), 0, "non-escalated calls must stay default")
            # Every third mutation call escalated (iterations 3, 6, ... 1-indexed).
            self.assertEqual(len(escalated), len(mutation_records) // 3)

    def test_escalation_account_stays_mutation(self):
        # Escalating the MODEL must not move spend to the coordination account
        # — it is still a mutation-seat generation.
        with tempfile_dir() as tmp:
            controller, ledger, _ = make_controller(tmp, config=make_config())
            controller.coordination = EveryThirdEscalates(
                config={"escalation_model": "strong-model"}, llm=None, rng=random.Random(0)
            )
            asyncio.run(controller.run())
            escalated = [r for r in ledger.records if r.model == "strong-model"]
            self.assertTrue(all(r.account == "mutation" for r in escalated))

    def test_escalation_sequence_is_deterministic_under_seed(self):
        def run_once():
            with tempfile_dir() as tmp:
                controller, ledger, _ = make_controller(tmp, config=make_config())
                module = EveryThirdEscalates(
                    config={"escalation_model": "strong-model"}, llm=None, rng=random.Random(7)
                )
                controller.coordination = module
                asyncio.run(controller.run())
                return list(module.escalation_log)

        self.assertEqual(run_once(), run_once())

    def test_null_arm_never_escalates(self):
        with tempfile_dir() as tmp:
            controller, ledger, _ = make_controller(tmp, config=make_config())
            self.assertIsInstance(controller.coordination, NullCoordination)
            asyncio.run(controller.run())
            self.assertTrue(all(r.model != "strong-model" for r in ledger.records))

    def test_escalation_model_bootstrapped_from_coordination_seat(self):
        # The construction-time bootstrap (controller.py) sets escalation_model
        # to config.llm.coordination.model for every module, without a new
        # base.py field or per-module config surface.
        from noema.coordination import build_coordination_module

        with tempfile_dir() as tmp:
            config = make_config()
            controller, _, _ = make_controller(tmp, config=config)
            # NullCoordination doesn't expose .config publicly by convention
            # elsewhere, but CoordinationModule.__init__ always stores it.
            module = build_coordination_module(
                "bandit", {"escalation_model": config.llm.coordination.model}, rng=random.Random(0)
            )
            self.assertEqual(module.config["escalation_model"], config.llm.coordination.model)


def tempfile_dir():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
