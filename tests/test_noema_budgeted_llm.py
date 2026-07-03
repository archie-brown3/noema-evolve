"""
Tests for noema.budget.llm.BudgetedLLM with a fake chat-completions client
"""

import asyncio
import unittest
from types import SimpleNamespace

from noema.budget.ledger import BudgetExhausted, TokenLedger
from noema.budget.llm import BudgetedLLM


def fake_response(content="response text", prompt_tokens=100, completion_tokens=40):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


class FakeClient:
    """Mimics openai.AsyncOpenAI: awaitable chat.completions.create(**params)"""

    def __init__(self, results):
        # results: list of responses or exceptions, consumed in order
        self._results = list(results)
        self.calls = []

        async def create(**params):
            self.calls.append(params)
            result = self._results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestBudgetedLLM(unittest.TestCase):
    def _llm(self, client, ledger=None, **kwargs):
        ledger = ledger or TokenLedger(total_budget_tokens=10_000)
        kwargs.setdefault("retry_delay", 0.0)
        llm = BudgetedLLM(
            model="test-model",
            ledger=ledger,
            account="mutation",
            tag="mutate",
            client=client,
            **kwargs,
        )
        return llm, ledger

    def test_successful_call_charges_exact_usage(self):
        client = FakeClient([fake_response(prompt_tokens=123, completion_tokens=45)])
        llm, ledger = self._llm(client)
        llm.iteration = 7

        result = asyncio.run(
            llm.generate_with_context("system msg", [{"role": "user", "content": "hi"}])
        )

        self.assertEqual(result, "response text")
        self.assertEqual(ledger.spent("mutation"), 168)
        rec = ledger.records[0]
        self.assertEqual(rec.prompt_tokens, 123)
        self.assertEqual(rec.completion_tokens, 45)
        self.assertEqual(rec.attempts, 1)
        self.assertEqual(rec.iteration, 7)
        self.assertEqual(rec.tag, "mutate")
        self.assertEqual(rec.account, "mutation")

    def test_system_message_prepended(self):
        client = FakeClient([fake_response()])
        llm, _ = self._llm(client)
        asyncio.run(llm.generate_with_context("sys", [{"role": "user", "content": "u"}]))
        messages = client.calls[0]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "sys"})
        self.assertEqual(messages[1], {"role": "user", "content": "u"})

    def test_retry_then_success_records_attempts(self):
        client = FakeClient(
            [RuntimeError("boom"), fake_response(prompt_tokens=10, completion_tokens=5)]
        )
        llm, ledger = self._llm(client, retries=2)

        result = asyncio.run(llm.generate_with_context("s", [{"role": "user", "content": "u"}]))

        self.assertEqual(result, "response text")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0].attempts, 2)
        self.assertEqual(ledger.spent(), 15)

    def test_all_retries_fail_raises_last_error(self):
        client = FakeClient([RuntimeError("a"), RuntimeError("b")])
        llm, ledger = self._llm(client, retries=1)

        with self.assertRaises(RuntimeError):
            asyncio.run(llm.generate_with_context("s", [{"role": "user", "content": "u"}]))
        self.assertEqual(len(ledger.records), 0)

    def test_preflight_raises_without_calling_api(self):
        ledger = TokenLedger(total_budget_tokens=100)
        client = FakeClient([fake_response(prompt_tokens=100, completion_tokens=0)])
        llm, _ = self._llm(client, ledger=ledger)

        # First call crosses the cap but is still returned and charged
        asyncio.run(llm.generate_with_context("s", [{"role": "user", "content": "u"}]))
        self.assertEqual(ledger.remaining(), 0)

        # Second call must raise BEFORE touching the API
        with self.assertRaises(BudgetExhausted):
            asyncio.run(llm.generate_with_context("s", [{"role": "user", "content": "u"}]))
        self.assertEqual(len(client.calls), 1)

    def test_generation_params_forwarded(self):
        client = FakeClient([fake_response()])
        llm, _ = self._llm(client, temperature=0.7, max_tokens=512, seed=42)
        asyncio.run(llm.generate_with_context("s", [{"role": "user", "content": "u"}]))
        params = client.calls[0]
        self.assertEqual(params["model"], "test-model")
        self.assertEqual(params["temperature"], 0.7)
        self.assertEqual(params["max_tokens"], 512)
        self.assertEqual(params["seed"], 42)
        self.assertNotIn("top_p", params)  # unset params are omitted

    def test_generate_wraps_prompt_as_user_message(self):
        client = FakeClient([fake_response()])
        llm, _ = self._llm(client)
        asyncio.run(llm.generate("just a prompt"))
        messages = client.calls[0]["messages"]
        self.assertEqual(messages, [{"role": "user", "content": "just a prompt"}])

    def test_missing_usage_charged_as_zero(self):
        # Defensive: some proxies omit usage; the call must still succeed
        response = fake_response()
        response.usage = None
        client = FakeClient([response])
        llm, ledger = self._llm(client)
        result = asyncio.run(llm.generate_with_context("s", [{"role": "user", "content": "u"}]))
        self.assertEqual(result, "response text")
        self.assertEqual(ledger.spent(), 0)
        self.assertEqual(len(ledger.records), 1)

    def test_is_openevolve_llm_interface(self):
        # Injectability into OpenEvolve components via LLMModelConfig.init_client
        from openevolve.llm.base import LLMInterface

        client = FakeClient([])
        llm, _ = self._llm(client)
        self.assertIsInstance(llm, LLMInterface)


if __name__ == "__main__":
    unittest.main()
