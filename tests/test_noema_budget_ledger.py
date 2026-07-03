"""
Tests for noema.budget.ledger (TokenLedger accounting semantics)
"""

import json
import os
import tempfile
import unittest

from noema.budget.ledger import BudgetExhausted, CallRecord, TokenLedger


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


if __name__ == "__main__":
    unittest.main()
