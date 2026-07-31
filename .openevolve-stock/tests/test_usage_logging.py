"""Tests for opt-in OpenAI response usage logging."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openevolve.llm.openai import OpenAILLM


def _model_config():
    return SimpleNamespace(
        name="deepseek/deepseek-v4-flash",
        system_message="system",
        temperature=0.0,
        top_p=None,
        max_tokens=128,
        timeout=30,
        retries=0,
        retry_delay=0,
        api_base="https://openrouter.ai/api/v1",
        api_key="test-key",
        random_seed=42,
        reasoning_effort=None,
        manual_mode=False,
    )


class TestUsageLogging(unittest.TestCase):
    def test_logs_response_usage_without_changing_returned_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "llm_calls.jsonl")
            response = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="```python\nreturn 1\n```"),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=7,
                    completion_tokens=5,
                    total_tokens=12,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
                    cost=0.0012,
                ),
            )
            with patch.dict(os.environ, {"OPENEVOLVE_USAGE_LOG": path}), patch(
                "openai.OpenAI"
            ) as client_factory:
                client_factory.return_value.chat.completions.create.return_value = response
                llm = OpenAILLM(_model_config())
                llm.mutation_index = 17
                result = asyncio.run(
                    llm._call_api(
                        {
                            "model": "deepseek/deepseek-v4-flash",
                            "messages": [{"role": "user", "content": "prompt"}],
                            "max_tokens": 128,
                        }
                    )
                )

            self.assertEqual(result, "```python\nreturn 1\n```")
            with open(path, encoding="utf-8") as handle:
                record = json.loads(handle.readline())
            self.assertEqual(record["model"], "deepseek/deepseek-v4-flash")
            self.assertEqual(record["mutation_index"], 17)
            self.assertEqual(record["prompt_tokens"], 7)
            self.assertEqual(record["completion_tokens"], 5)
            self.assertEqual(record["total_tokens"], 12)
            self.assertEqual(record["reasoning_tokens"], 2)
            self.assertEqual(record["cost"], 0.0012)
            self.assertEqual(record["finish_reason"], "stop")
            self.assertTrue(record["succeeded"])

    def test_logging_is_disabled_when_environment_variable_is_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "llm_calls.jsonl")
            response = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="answer"),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )
            with patch.dict(os.environ, {}, clear=True), patch("openai.OpenAI") as client_factory:
                client_factory.return_value.chat.completions.create.return_value = response
                llm = OpenAILLM(_model_config())
                self.assertEqual(
                    asyncio.run(
                        llm._call_api(
                            {
                                "model": "deepseek/deepseek-v4-flash",
                                "messages": [{"role": "user", "content": "prompt"}],
                            }
                        )
                    ),
                    "answer",
                )
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
