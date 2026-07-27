"""Controller-owned escalation wiring (task 0107, "B").

The EscalationPolicy is owned by the controller, not any arm: after the
coordination module returns its Advice, the controller builds an
EscalationContext from state it already tracks (fitness history, ledger tokens,
rolling validity) and sets advice.model. So escalation is an arm-agnostic
modifier — even the null arm escalates without NullCoordination changing.

These tests drive that wiring; they fail before it exists.
"""

import asyncio
import tempfile
import unittest
from types import SimpleNamespace

from noema.config import BudgetConfig, CoordinationConfig, EscalationConfig
from noema.coordination import NullCoordination

from tests.test_noema_controller import make_config, make_controller


def esc_config(config_overrides=None, **esc_overrides):
    """A make_config with a null module carrying an escalation policy that
    routes to an unambiguous 'strong-model'. `config_overrides` sets other
    NoemaConfig fields (e.g. budget, diff mode)."""
    esc = EscalationConfig(escalation_model="strong-model", **esc_overrides)
    overrides = dict(config_overrides or {})
    overrides["coordination"] = CoordinationConfig(module="null", escalation=esc)
    return make_config(**overrides)


class InvalidFakeClient:
    """Fake AsyncOpenAI that always returns an unparseable response, so every
    mutation is invalid (no code block) — drives the invalidity trigger."""

    def __init__(self, prompt_tokens=100, completion_tokens=40):
        self.calls = []

        async def create(**params):
            self.calls.append(params)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="no code here"))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestControllerEscalation(unittest.TestCase):
    def test_no_escalation_config_is_byte_identical(self):
        # A run with no escalation config never routes to any other model.
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, _ = make_controller(tmp, config=make_config())
            asyncio.run(controller.run())
            self.assertTrue(all(r.model == "fake-model" for r in ledger.records))

    def test_budget_fraction_escalates_after_the_fraction_is_spent(self):
        # budget_fraction needs no new signal tracking (ledger only). With a low
        # fraction, late-run mutations flip to the strong model.
        # Small budget (1000 tokens; ~140/mutation over 6 iterations = 840 total,
        # no exhaustion) so 30% is genuinely crossed mid-run.
        with tempfile.TemporaryDirectory() as tmp:
            config = esc_config(
                {"budget": BudgetConfig(total_tokens=1000)},
                trigger="budget_fraction", fraction=0.3, burst_length=10, cooldown_mutations=0,
            )
            controller, ledger, _ = make_controller(tmp, config=config, budget_tokens=1000)
            asyncio.run(controller.run())
            escalated = [r for r in ledger.records if r.model == "strong-model"]
            unescalated = [r for r in ledger.records if r.model == "fake-model"]
            self.assertGreater(len(escalated), 0, "expected late-run escalation")
            self.assertGreater(len(unescalated), 0, "early mutations stay on the base model")
            self.assertTrue(all(r.account == "mutation" for r in escalated))

    def test_invalidity_trigger_is_wired(self):
        # Every mutation is invalid → invalidity rate saturates → the invalidity
        # trigger escalates. Proves the rolling validity signal reaches the policy.
        # Diff mode: a response with no SEARCH/REPLACE block parses to no child
        # (invalid), unlike full-rewrite mode which falls back to raw text.
        with tempfile.TemporaryDirectory() as tmp:
            config = esc_config(
                {"diff_based_evolution": True},
                trigger="invalidity", threshold=0.5, window=1, burst_length=1, cooldown_mutations=0,
            )
            controller, ledger, _ = make_controller(
                tmp, config=config, client=InvalidFakeClient()
            )
            asyncio.run(controller.run())
            escalated = [r for r in ledger.records if r.model == "strong-model"]
            self.assertGreater(len(escalated), 0, "invalidity trigger never fired")

    def test_escalation_is_deterministic_under_seed(self):
        def escalated_iterations():
            with tempfile.TemporaryDirectory() as tmp:
                config = esc_config(
                    trigger="random", probability=0.5, burst_length=1, cooldown_mutations=0
                )
                controller, ledger, _ = make_controller(tmp, config=config)
                asyncio.run(controller.run())
                return [r.iteration for r in ledger.records if r.model == "strong-model"]

        self.assertEqual(escalated_iterations(), escalated_iterations())

    def test_escalation_state_survives_checkpoint_resume(self):
        # A run's escalation state (policy burst/cooldown, rolling signals, RNG)
        # must round-trip through a checkpoint so a resumed run continues coherently.
        with tempfile.TemporaryDirectory() as tmp:
            config = esc_config(
                {"budget": BudgetConfig(total_tokens=1000), "checkpoint_interval": 3},
                trigger="budget_fraction", fraction=0.3, burst_length=10, cooldown_mutations=0,
            )
            controller, _, _ = make_controller(tmp, config=config, budget_tokens=1000)
            asyncio.run(controller.run(iterations=3))
            ckpt = f"{tmp}/output/checkpoints/checkpoint_2"

            resumed, _, _ = make_controller(tmp, config=config, budget_tokens=1000)
            resumed.load_checkpoint(ckpt)
            self.assertEqual(
                resumed.escalation.state_dict(), controller.escalation.state_dict()
            )
            self.assertEqual(
                list(resumed._esc_recent_valid), list(controller._esc_recent_valid)
            )

    def test_null_module_still_escalates(self):
        # The injected coordination module is a plain NullCoordination — escalation
        # is applied by the controller, so null escalates without any arm change.
        with tempfile.TemporaryDirectory() as tmp:
            config = esc_config(
                trigger="budget_fraction", fraction=0.0, burst_length=10, cooldown_mutations=0
            )
            controller, ledger, _ = make_controller(tmp, config=config)
            self.assertIsInstance(controller.coordination, NullCoordination)
            asyncio.run(controller.run())
            self.assertTrue(any(r.model == "strong-model" for r in ledger.records))


if __name__ == "__main__":
    unittest.main()
