"""Escalation policy layer (task 0107, on top of the Advice.model plumbing).

The policy decides WHEN a mutation generation escalates to the stronger model.
It is a pure, deterministic unit: fed an EscalationContext snapshot each
mutation, it returns the escalation model name (during a burst) or None. Five
pluggable triggers decide when a burst starts; burst_length fixes how long it
lasts; a cooldown prevents thrashing before it can re-trigger.

These tests pin the behaviour before any of it is implemented.
"""

import random
import unittest

from noema.coordination.escalation import (
    EscalationConfig,
    EscalationContext,
    EscalationPolicy,
)


def make_policy(**overrides):
    """An EscalationPolicy with a named strong model and a fixed seed."""
    cfg = EscalationConfig(escalation_model="strong-model", **overrides)
    return EscalationPolicy(cfg, rng=random.Random(0))


class TestNoTrigger(unittest.TestCase):
    def test_condition_not_met_never_escalates(self):
        # budget_fraction trigger at 0.7, but only 50% of budget spent.
        policy = make_policy(trigger="budget_fraction", fraction=0.7)
        ctx = EscalationContext(tokens_spent=50, tokens_budget=100)
        self.assertIsNone(policy.step(ctx))

    def test_no_escalation_model_disables_escalation(self):
        # With no strong model to escalate to, the policy is inert even when
        # the condition is met — nothing to route to.
        cfg = EscalationConfig(
            trigger="budget_fraction", fraction=0.7, escalation_model=None
        )
        policy = EscalationPolicy(cfg, rng=random.Random(0))
        ctx = EscalationContext(tokens_spent=90, tokens_budget=100)
        self.assertIsNone(policy.step(ctx))


class TestBurst(unittest.TestCase):
    def test_trigger_returns_the_escalation_model(self):
        policy = make_policy(trigger="budget_fraction", fraction=0.7, burst_length=1)
        ctx = EscalationContext(tokens_spent=80, tokens_budget=100)
        self.assertEqual(policy.step(ctx), "strong-model")

    def test_burst_lasts_exactly_burst_length_calls(self):
        # Condition stays true throughout; the burst is a FIXED length, then it
        # reverts and enters cooldown, so escalation stops even while the
        # condition still holds.
        policy = make_policy(
            trigger="budget_fraction", fraction=0.7, burst_length=3, cooldown_mutations=5
        )
        ctx = EscalationContext(tokens_spent=80, tokens_budget=100)
        results = [policy.step(ctx) for _ in range(4)]
        self.assertEqual(
            results, ["strong-model", "strong-model", "strong-model", None]
        )


class TestCooldown(unittest.TestCase):
    def test_cooldown_blocks_retrigger(self):
        # After a 1-call burst, the cooldown holds escalation off for
        # cooldown_mutations calls even though the condition is still met.
        policy = make_policy(
            trigger="budget_fraction", fraction=0.7, burst_length=1, cooldown_mutations=2
        )
        ctx = EscalationContext(tokens_spent=80, tokens_budget=100)
        self.assertEqual(policy.step(ctx), "strong-model")  # burst
        self.assertIsNone(policy.step(ctx))  # cooldown 1
        self.assertIsNone(policy.step(ctx))  # cooldown 2

    def test_can_retrigger_after_cooldown_expires(self):
        # Repeating escalation: once the cooldown elapses and the condition is
        # still met, a new burst starts.
        policy = make_policy(
            trigger="budget_fraction", fraction=0.7, burst_length=1, cooldown_mutations=2
        )
        ctx = EscalationContext(tokens_spent=80, tokens_budget=100)
        results = [policy.step(ctx) for _ in range(4)]
        # burst, cooldown, cooldown, burst-again
        self.assertEqual(results, ["strong-model", None, None, "strong-model"])


class TestTriggers(unittest.TestCase):
    def test_plateau_triggers_when_best_is_flat_over_window(self):
        policy = make_policy(trigger="plateau", window=3, min_delta=0.001, burst_length=1)
        flat = EscalationContext(best_fitness_history=(0.5, 0.5, 0.5))
        self.assertEqual(policy.step(flat), "strong-model")

    def test_plateau_does_not_trigger_while_improving(self):
        policy = make_policy(trigger="plateau", window=3, min_delta=0.001, burst_length=1)
        improving = EscalationContext(best_fitness_history=(0.5, 0.6, 0.7))
        self.assertIsNone(policy.step(improving))

    def test_plateau_needs_a_full_window_of_history(self):
        policy = make_policy(trigger="plateau", window=3, min_delta=0.001, burst_length=1)
        too_short = EscalationContext(best_fitness_history=(0.5, 0.5))
        self.assertIsNone(policy.step(too_short))

    def test_invalidity_triggers_above_threshold(self):
        policy = make_policy(trigger="invalidity", threshold=0.5, burst_length=1)
        self.assertEqual(
            policy.step(EscalationContext(invalidity_rate=0.8)), "strong-model"
        )

    def test_invalidity_does_not_trigger_below_threshold(self):
        policy = make_policy(trigger="invalidity", threshold=0.5, burst_length=1)
        self.assertIsNone(policy.step(EscalationContext(invalidity_rate=0.2)))

    def test_budget_fraction_triggers_at_or_above_fraction(self):
        policy = make_policy(trigger="budget_fraction", fraction=0.7, burst_length=1)
        self.assertEqual(
            policy.step(EscalationContext(tokens_spent=70, tokens_budget=100)),
            "strong-model",
        )

    def test_diversity_triggers_when_variance_low(self):
        policy = make_policy(trigger="diversity", threshold=0.01, window=4, burst_length=1)
        low_var = EscalationContext(recent_scores=(0.50, 0.50, 0.51, 0.50))
        self.assertEqual(policy.step(low_var), "strong-model")

    def test_diversity_does_not_trigger_when_variance_high(self):
        policy = make_policy(trigger="diversity", threshold=0.01, window=4, burst_length=1)
        high_var = EscalationContext(recent_scores=(0.1, 0.9, 0.2, 0.8))
        self.assertIsNone(policy.step(high_var))

    def test_random_trigger_respects_probability_zero(self):
        policy = make_policy(trigger="random", probability=0.0, burst_length=1)
        self.assertIsNone(policy.step(EscalationContext()))

    def test_random_trigger_respects_probability_one(self):
        policy = make_policy(trigger="random", probability=1.0, burst_length=1)
        self.assertEqual(policy.step(EscalationContext()), "strong-model")


class TestDeterminism(unittest.TestCase):
    def test_random_trigger_is_deterministic_under_seed(self):
        def run():
            cfg = EscalationConfig(
                trigger="random",
                probability=0.5,
                burst_length=1,
                cooldown_mutations=0,
                escalation_model="strong-model",
            )
            policy = EscalationPolicy(cfg, rng=random.Random(7))
            return [policy.step(EscalationContext()) for _ in range(20)]

        self.assertEqual(run(), run())

    def test_random_trigger_actually_fires_sometimes(self):
        # Guard against a degenerate always-None (which would also be
        # deterministic). At p=0.5 over 20 steps some should escalate.
        cfg = EscalationConfig(
            trigger="random",
            probability=0.5,
            burst_length=1,
            cooldown_mutations=0,
            escalation_model="strong-model",
        )
        policy = EscalationPolicy(cfg, rng=random.Random(7))
        results = [policy.step(EscalationContext()) for _ in range(20)]
        self.assertIn("strong-model", results)
        self.assertIn(None, results)


class TestCheckpointRoundTrip(unittest.TestCase):
    def test_state_survives_serialization_mid_burst(self):
        # Mid-burst state (burst_remaining, cooldown, trigger_count) must
        # round-trip so a checkpoint resume continues the burst correctly.
        policy = make_policy(
            trigger="budget_fraction", fraction=0.7, burst_length=3, cooldown_mutations=5
        )
        ctx = EscalationContext(tokens_spent=80, tokens_budget=100)
        policy.step(ctx)  # start burst (1 of 3 emitted)

        restored = make_policy(
            trigger="budget_fraction", fraction=0.7, burst_length=3, cooldown_mutations=5
        )
        restored.load_state_dict(policy.state_dict())

        # Both should emit the remaining two burst calls, then None.
        idle = EscalationContext(tokens_spent=0, tokens_budget=100)  # condition false
        self.assertEqual(
            [restored.step(idle) for _ in range(3)],
            ["strong-model", "strong-model", None],
        )


if __name__ == "__main__":
    unittest.main()
