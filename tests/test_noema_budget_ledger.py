"""
Tests for noema.budget.ledger (TokenLedger accounting semantics), plus the
controller-driven metering contract for retry attempts (task 0062).
"""

import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import BudgetExhausted, CallRecord, TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import BudgetConfig, NoemaConfig
from noema.controller import NoemaController
from noema.coordination.base import NullCoordination


def record(account="mutation", prompt=100, completion=50, **kwargs):
    return CallRecord(
        account=account,
        tag=kwargs.pop("tag", "test"),
        model=kwargs.pop("model", "test-model"),
        prompt_tokens=prompt,
        completion_tokens=completion,
        **kwargs,
    )


class TestTokenLedger(unittest.TestCase):
    def test_charge_and_spent_per_account(self):
        ledger = TokenLedger(total_budget_tokens=10_000)
        ledger.charge(record("mutation", prompt=100, completion=50))
        ledger.charge(record("coordination", prompt=30, completion=20))
        ledger.charge(record("mutation", prompt=10, completion=10))

        self.assertEqual(ledger.spent("mutation"), 170)
        self.assertEqual(ledger.spent("coordination"), 50)
        self.assertEqual(ledger.spent(), 220)
        self.assertEqual(ledger.remaining(), 10_000 - 220)

    def test_shared_pool_across_accounts(self):
        # Coordination spending reduces what mutation can use — that is the
        # experimental point of a single shared pool
        ledger = TokenLedger(total_budget_tokens=1_000)
        ledger.charge(record("coordination", prompt=600, completion=0))
        self.assertEqual(ledger.remaining("mutation"), 400)

    def test_account_cap_limits_below_pool(self):
        ledger = TokenLedger(total_budget_tokens=10_000, account_caps={"coordination": 100})
        self.assertEqual(ledger.remaining("coordination"), 100)
        ledger.charge(record("coordination", prompt=80, completion=0))
        self.assertEqual(ledger.remaining("coordination"), 20)
        # Uncapped account still sees the shared pool
        self.assertEqual(ledger.remaining("mutation"), 10_000 - 80)

    def test_ensure_raises_when_pool_exhausted(self):
        ledger = TokenLedger(total_budget_tokens=100)
        ledger.ensure("mutation")  # fine before spending
        ledger.charge(record("mutation", prompt=100, completion=0))
        with self.assertRaises(BudgetExhausted) as ctx:
            ledger.ensure("mutation")
        self.assertEqual(ctx.exception.spent, 100)
        self.assertEqual(ctx.exception.cap, 100)

    def test_ensure_raises_on_account_cap(self):
        ledger = TokenLedger(total_budget_tokens=10_000, account_caps={"coordination": 50})
        ledger.charge(record("coordination", prompt=50, completion=0))
        with self.assertRaises(BudgetExhausted) as ctx:
            ledger.ensure("coordination")
        self.assertEqual(ctx.exception.account, "coordination")
        self.assertEqual(ctx.exception.cap, 50)
        # Other account unaffected
        ledger.ensure("mutation")

    def test_shared_pool_exhaustion_does_not_blame_the_asking_account(self):
        # Task 0067. With NO per-account cap the accounts draw on one shared pool,
        # so what runs out is the POOL. The old message named whichever account
        # happened to make the next call and reported the pool's TOTAL spend
        # against it: the 2026-07-13 run logged "Budget exhausted for account
        # 'coordination': spent 1013477 of cap 1000000" while coordination itself
        # had spent a small fraction of that. Mislabelled metering is a triad
        # concern, not cosmetics — it misattributes which account overran.
        ledger = TokenLedger(total_budget_tokens=100)  # no account_caps
        ledger.charge(record("mutation", prompt=95, completion=0))  # mutation burned it
        ledger.charge(record("coordination", prompt=10, completion=0))
        with self.assertRaises(BudgetExhausted) as ctx:
            ledger.ensure("coordination")  # coordination merely asks next

        e = ctx.exception
        self.assertTrue(e.shared_pool)
        self.assertEqual(e.spent, 105)  # the POOL total, not coordination's 10
        self.assertEqual(e.cap, 100)
        msg = str(e)
        self.assertIn("Total token budget exhausted (shared pool)", msg)
        self.assertIn("that account is not itself capped", msg)
        # It must NOT read as though the coordination account overran its own cap
        self.assertNotIn("Budget exhausted for account 'coordination'", msg)

    def test_per_account_cap_message_still_names_the_account(self):
        # The other branch is unchanged: a real per-account overrun still says so.
        ledger = TokenLedger(total_budget_tokens=10_000, account_caps={"coordination": 50})
        ledger.charge(record("coordination", prompt=50, completion=0))
        with self.assertRaises(BudgetExhausted) as ctx:
            ledger.ensure("coordination")
        e = ctx.exception
        self.assertFalse(e.shared_pool)
        self.assertIn("Budget exhausted for account 'coordination'", str(e))
        self.assertEqual(e.spent, 50)  # the ACCOUNT's spend, not the pool's

    def test_charge_never_raises_and_can_go_negative(self):
        # The call that crosses the cap is still recorded; the next ensure() raises
        ledger = TokenLedger(total_budget_tokens=100)
        remaining = ledger.charge(record("mutation", prompt=150, completion=0))
        self.assertEqual(remaining, -50)
        with self.assertRaises(BudgetExhausted):
            ledger.ensure("mutation")

    def test_snapshot_restore_round_trip(self):
        ledger = TokenLedger(total_budget_tokens=1_000, account_caps={"coordination": 200})
        ledger.charge(record("mutation", prompt=100, completion=50, iteration=3))
        ledger.charge(record("coordination", prompt=10, completion=5, iteration=4))
        snap = ledger.snapshot()

        # Snapshot must be JSON-serializable (it goes into checkpoints)
        snap = json.loads(json.dumps(snap))

        restored = TokenLedger(total_budget_tokens=1)
        restored.restore(snap)
        self.assertEqual(restored.total_budget_tokens, 1_000)
        self.assertEqual(restored.spent("mutation"), 150)
        self.assertEqual(restored.spent("coordination"), 15)
        self.assertEqual(restored.remaining(), 1_000 - 165)
        self.assertEqual(len(restored.records), 2)
        self.assertEqual(restored.records[0].iteration, 3)
        self.assertEqual(restored.account_caps, {"coordination": 200})

    def test_jsonl_log_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "calls.jsonl")
            ledger = TokenLedger(total_budget_tokens=1_000, log_path=log_path)
            ledger.charge(record("mutation", prompt=1, completion=2, tag="mutate"))
            ledger.charge(record("coordination", prompt=3, completion=4, tag="extract"))

            with open(log_path) as f:
                lines = [json.loads(line) for line in f]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["tag"], "mutate")
            self.assertEqual(lines[1]["prompt_tokens"], 3)

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            TokenLedger(total_budget_tokens=0)


class TestRetrySpendMetering(unittest.TestCase):
    """Every retry attempt under retry_on="non_improvement" is metered
    per-attempt on the mutation account, and BudgetExhausted raised mid-retry
    stops the run cleanly (task 0062; guarantee triad: metering integrity)."""

    EVAL = (
        "import re\n"
        "def evaluate(program_path):\n"
        "    code = open(program_path).read()\n"
        "    m = re.search(r'return (\\d+(?:\\.\\d+)?)', code)\n"
        "    return {'combined_score': min(1.0, (float(m.group(1)) if m else 0.0) / 10.0)}\n"
    )

    def _controller(self, tmp, budget_tokens):
        # Fake client always emits a valid-but-worse child ("return 0.5" scores
        # 0.05 vs the parent seed's 0.1), so every attempt is a non-improvement
        # retry. 10 + 5 = 15 tokens per call.
        client = SimpleNamespace(calls=[])

        async def create(**params):
            client.calls.append(params)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="```python\ndef f():\n    return 0.5\n```"))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        eval_path = os.path.join(tmp, "evaluator.py")
        with open(eval_path, "w") as f:
            f.write(self.EVAL)
        ledger = TokenLedger(total_budget_tokens=budget_tokens)
        controller = NoemaController(
            config=NoemaConfig(
                retry_enabled=True, retry_cap=1, retry_on="non_improvement",
                database=DatabaseConfig(in_memory=True, num_islands=2,
                                        population_size=50, random_seed=42,
                                        migration_interval=1000),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30,
                                          max_retries=0),
                budget=BudgetConfig(total_tokens=budget_tokens),
            ),
            evaluation_file=eval_path,
            initial_program_code="def f():\n    return 1\n",
            output_dir=os.path.join(tmp, "output"),
            mutation_llm=BudgetedLLM(model="fake-model", ledger=ledger,
                                     account="mutation", tag="mutate",
                                     client=client, retries=0, retry_delay=0.0),
            coordination=NullCoordination(),
            ledger=ledger,
        )
        return controller, ledger, client

    def test_each_retry_attempt_metered_per_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, client = self._controller(tmp, budget_tokens=1_000_000)
            asyncio.run(controller.run(iterations=1))
            # cap 1 -> initial + 1 retry = 2 calls, each its own ledger record
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(ledger.spent("mutation"), 2 * 15)
            self.assertEqual([r.account for r in ledger.records], ["mutation"] * 2)
            self.assertEqual([r.total_tokens for r in ledger.records], [15, 15])

    def test_budget_exhausted_mid_retry_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Exactly one attempt's worth: the pool hits zero after the first
            # call, so the non-improvement RETRY is what trips BudgetExhausted.
            controller, ledger, client = self._controller(tmp, budget_tokens=15)
            asyncio.run(controller.run(iterations=5))  # must not raise
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(ledger.spent("mutation"), 15)
            self.assertEqual(ledger.remaining(), 0)


if __name__ == "__main__":
    unittest.main()
