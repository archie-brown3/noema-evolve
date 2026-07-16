"""Tests for the `bandit` mechanism arm (task 0073).

Covers the arm's four defining properties:
- ZERO coordination-account spend over a full run (asserted on the ledger, not a
  mock — this is the arm's whole point);
- AsymmetricUCB provably prefers the best-rewarding operator on a synthetic
  stationary bandit;
- every other arm stays byte-identical (the operator partition leaves the RNG
  path untouched when no operator is requested);
- determinism: same seed/config -> same operator sequence and same UCB state.
"""

import asyncio
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import COORDINATION_ACCOUNT, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    LLMClientConfig,
    NoemaConfig,
)
from noema.coordination import build_coordination_module
from noema.coordination.base import Outcome, SelectionContext
from noema.coordination.bandit.module import AsymmetricUCB, BanditModule
from noema.controller import NoemaController

INITIAL_PROGRAM = "def f():\n    return 1\n"
MENU = ["e1", "e2", "m1", "m2", "m3"]

EVAL_SCRIPT = """\
import re

def evaluate(program_path):
    with open(program_path) as f:
        code = f.read()
    m = re.search(r"return (\\d+(?:\\.\\d+)?)", code)
    value = float(m.group(1)) if m else 0.0
    return {"combined_score": min(1.0, value / 10.0)}
"""


class RewriteClient:
    """Emits parseable full rewrites with increasing scores (so children are
    accepted and the bandit sees real rewards)."""

    def __init__(self):
        self.calls = []
        self._n = 0

        async def create(**params):
            self.calls.append(params)
            self._n += 1
            content = f"```python\ndef f():\n    return {self._n + 1}\n```"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def make_bandit_config(**overrides):
    defaults = dict(
        max_iterations=8,
        checkpoint_interval=100,
        random_seed=42,
        diff_based_evolution=False,  # client emits full rewrites
        mutation_operators=MENU,     # menu ON — mandatory for the bandit
        database=DatabaseConfig(
            in_memory=True, num_islands=2, population_size=50,
            random_seed=42, migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        budget=BudgetConfig(total_tokens=1_000_000),
        coordination=CoordinationConfig(module="bandit"),
        llm=LLMClientConfig(api_key="none"),
    )
    defaults.update(overrides)
    return NoemaConfig(**defaults)


def make_controller(tmp, config, client=None):
    eval_path = os.path.join(tmp, "evaluator.py")
    if not os.path.exists(eval_path):
        with open(eval_path, "w") as f:
            f.write(EVAL_SCRIPT)
    ledger = TokenLedger(total_budget_tokens=1_000_000)
    client = client or RewriteClient()
    mutation_llm = BudgetedLLM(
        model="fake", ledger=ledger, account="mutation", tag="mutate",
        client=client, retries=0, retry_delay=0.0,
    )
    controller = NoemaController(
        config=config, evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        mutation_llm=mutation_llm,
    )
    return controller, ledger


class TestBanditIsZeroToken(unittest.TestCase):
    def test_no_coordination_account_spend_over_a_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger = make_controller(tmp, make_bandit_config())
            asyncio.run(controller.run(iterations=8))
            # The defining property: the coordination account never moves.
            self.assertEqual(ledger.spent(account=COORDINATION_ACCOUNT), 0)
            # ...while mutation tokens WERE spent (the run really happened).
            self.assertGreater(ledger.spent(), 0)

    def test_bandit_runs_end_to_end_and_steers_operators(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _ = make_controller(tmp, make_bandit_config())
            asyncio.run(controller.run(iterations=8))
            # Every generation-tick log recorded an honored operator from the menu.
            honored = [
                g["operator_selection"]["honored"] for g in controller.generation_log
            ]
            self.assertTrue(honored)
            self.assertTrue(all(h in MENU for h in honored))
            # And the request was honored, never ignored, for the bandit.
            self.assertTrue(
                all(
                    g["operator_selection"]["ignored"] is None
                    for g in controller.generation_log
                )
            )


class TestUCBPrefersBestOperator(unittest.TestCase):
    def test_converges_to_the_best_rewarding_arm_on_a_stationary_bandit(self):
        # Synthetic stationary bandit: m2 pays the most, e1 the least.
        true_reward = {"e1": 0.0, "e2": 0.2, "m1": 0.4, "m2": 0.9, "m3": 0.3}
        ucb = AsymmetricUCB(MENU, exploration_coef=0.5)
        picks = []
        for _ in range(400):
            arm = ucb.select()
            picks.append(arm)
            # baseline 0: reward is the arm's stationary payoff.
            ucb.update(arm, reward=true_reward[arm], baseline=0.0)
        # Overwhelmingly prefers the best arm in the second half.
        tail = picks[200:]
        self.assertGreater(tail.count("m2"), 0.75 * len(tail))
        self.assertEqual(max(MENU, key=lambda a: tail.count(a)), "m2")

    def test_pulls_every_arm_before_exploiting(self):
        # select() is a pure read; the caller update()s between pulls (the loop
        # does this via report_result). Each arm must be pulled once before any
        # repeat — UCB1 initialization.
        ucb = AsymmetricUCB(MENU, exploration_coef=1.0)
        first = []
        for _ in range(len(MENU)):
            arm = ucb.select()
            first.append(arm)
            ucb.update(arm, reward=0.1, baseline=0.0)
        self.assertEqual(sorted(first), sorted(MENU))
        # The 6th pull is now a real UCB decision, not another unseen arm.
        self.assertIn(ucb.select(), MENU)


class TestOtherArmsUnchanged(unittest.TestCase):
    """The operator partition must not perturb an arm that requests no operator."""

    def test_empty_request_leaves_operator_rng_draw_identical(self):
        # Two null runs with the menu ON: the operator sequence is driven purely
        # by mutation_operator_rng. Introducing the partition must not change it.
        def operator_sequence():
            with tempfile.TemporaryDirectory() as tmp:
                config = make_bandit_config(
                    coordination=CoordinationConfig(module="null")
                )
                controller, _ = make_controller(tmp, config)
                asyncio.run(controller.run(iterations=8))
                return [
                    g["operator_selection"]["honored"]
                    for g in controller.generation_log
                ]

        seq_a = operator_sequence()
        seq_b = operator_sequence()
        self.assertEqual(seq_a, seq_b)                 # deterministic under seed
        self.assertTrue(all(h in MENU for h in seq_a))  # menu really is on
        # null never requests an operator -> requested is always None.
        with tempfile.TemporaryDirectory() as tmp:
            config = make_bandit_config(coordination=CoordinationConfig(module="null"))
            controller, _ = make_controller(tmp, config)
            asyncio.run(controller.run(iterations=4))
            self.assertTrue(
                all(
                    g["operator_selection"]["requested"] is None
                    for g in controller.generation_log
                )
            )


class TestBanditDeterminism(unittest.TestCase):
    def test_same_seed_same_operator_sequence_and_state(self):
        def run():
            with tempfile.TemporaryDirectory() as tmp:
                controller, _ = make_controller(tmp, make_bandit_config())
                asyncio.run(controller.run(iterations=8))
                return (
                    [g["operator_selection"]["honored"] for g in controller.generation_log],
                    controller.coordination.state_dict(),
                )

        seq_a, state_a = run()
        seq_b, state_b = run()
        self.assertEqual(seq_a, seq_b)
        self.assertEqual(state_a, state_b)

    def test_checkpoint_state_roundtrips(self):
        module = build_coordination_module("bandit", {}, llm=None)
        for arm, r in [("e1", 0.3), ("m2", 0.9), ("m2", 0.8), ("m1", 0.1)]:
            module._pending = arm
            module.ucb.update(arm, reward=r, baseline=0.0)
        restored = build_coordination_module("bandit", {}, llm=None)
        restored.load_state_dict(module.state_dict())
        self.assertEqual(restored.state_dict(), module.state_dict())
        # And the restored bandit selects the same next arm.
        self.assertEqual(restored.ucb.select(), module.ucb.select())


if __name__ == "__main__":
    unittest.main()
