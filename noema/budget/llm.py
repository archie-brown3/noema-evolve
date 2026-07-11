"""
Ledger-metered LLM client.

BudgetedLLM is the ONLY object in noema that talks to the chat-completions API.
It exists because OpenEvolve's OpenAILLM discards `response.usage`
(openevolve/llm/openai.py:_call_api returns only the message content), so wrapping
it could only estimate token counts. BudgetedLLM makes the API call itself with the
OpenAI SDK, reads exact usage from every billed attempt, and charges the ledger.

It implements openevolve.llm.base.LLMInterface, so it is also drop-in injectable
into any OpenEvolve component via LLMModelConfig.init_client if a borrowed
component with an internal ensemble is ever reused.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional

from openevolve.llm.base import LLMInterface

from noema.budget.ledger import CallRecord, TokenLedger

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # rough fallback estimate for servers that omit real usage counts


def _estimate_token_count(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


class BudgetedLLM(LLMInterface):
    """
    Async chat-completions client that meters every billed attempt into a TokenLedger.

    Retries are handled here (the SDK's own retries are disabled with max_retries=0)
    so that every attempt that returns usage is charged. Failed attempts that raise
    before a response exists cannot report usage; they are counted in the record's
    `attempts` field so the discrepancy is visible when reconciling against provider
    dashboards.

    Args:
        model: Model name for the chat-completions API.
        ledger: Shared TokenLedger to charge.
        account: Ledger account this client draws from ("mutation" | "coordination").
        tag: Label recorded on every call (e.g. "mutate", "hifo.extract_insights").
        api_base / api_key / timeout: Client configuration (ignored when `client` given).
        temperature / top_p / max_tokens / seed: Default generation parameters,
            overridable per call via kwargs.
        retries / retry_delay: noema-level retry policy.
        client: Injectable pre-built client (used by tests); must expose
            `chat.completions.create(**params)` as an awaitable.
    """

    def __init__(
        self,
        model: str,
        ledger: TokenLedger,
        account: str,
        tag: str = "",
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        seed: Optional[int] = None,
        timeout: Optional[float] = 60.0,
        retries: int = 3,
        retry_delay: float = 5.0,
        client=None,
    ):
        self.model = model
        self.ledger = ledger
        self.account = account
        self.tag = tag
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.seed = seed
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay

        # Set by the controller each iteration so CallRecords carry provenance
        self.iteration: int = -1

        if client is not None:
            self.client = client
        else:
            import openai

            # max_retries=0: all retries go through our loop so each billed
            # attempt is visible to the ledger
            self.client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=api_base,
                timeout=timeout,
                max_retries=0,
            )

    async def generate(self, prompt: str, **kwargs) -> str:
        return await self.generate_with_context(
            system_message=kwargs.pop("system_message", ""),
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )

    async def generate_with_context(
        self, system_message: str, messages: List[Dict[str, str]], **kwargs
    ) -> str:
        """
        Make a metered chat-completions call.

        Raises BudgetExhausted (from ledger.ensure) before issuing a request once
        the account's budget is used up. The response that crosses the cap is still
        returned and charged; the next call raises.
        """
        formatted_messages = []
        if system_message:
            formatted_messages.append({"role": "system", "content": system_message})
        formatted_messages.extend(messages)

        params = {
            "model": self.model,
            "messages": formatted_messages,
        }
        for name, default in (
            ("temperature", self.temperature),
            ("top_p", self.top_p),
            ("max_tokens", self.max_tokens),
            ("seed", self.seed),
        ):
            value = kwargs.get(name, default)
            if value is not None:
                params[name] = value

        retries = kwargs.get("retries", self.retries)
        retry_delay = kwargs.get("retry_delay", self.retry_delay)
        tag = kwargs.get("tag", self.tag)

        start = time.time()
        last_exception: Optional[Exception] = None
        for attempt in range(retries + 1):
            # Pre-flight on every attempt: retrying is also spending
            self.ledger.ensure(self.account)
            try:
                response = await self.client.chat.completions.create(**params)
            except Exception as e:
                last_exception = e
                if attempt < retries:
                    logger.warning(
                        f"LLM call failed (attempt {attempt + 1}/{retries + 1}): {e}. Retrying..."
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                raise

            usage = getattr(response, "usage", None)
            content = response.choices[0].message.content

            # Some OpenAI-compatible local servers (llama.cpp/vLLM) return a
            # `usage` envelope whose prompt_tokens/completion_tokens fields are
            # null instead of omitting `usage` altogether. Estimate only the
            # fields that came back null so the ledger never records a silent
            # zero for tokens that were actually spent; when `usage` is absent
            # entirely there is nothing to estimate from, so it is charged zero.
            prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
            completion_tokens = (
                getattr(usage, "completion_tokens", None) if usage is not None else None
            )
            estimated = usage is not None and (prompt_tokens is None or completion_tokens is None)
            if prompt_tokens is None:
                prompt_tokens = (
                    _estimate_token_count(
                        "".join(m.get("content", "") for m in formatted_messages)
                    )
                    if usage is not None
                    else 0
                )
            if completion_tokens is None:
                completion_tokens = _estimate_token_count(content) if usage is not None else 0

            self.ledger.charge(
                CallRecord(
                    account=self.account,
                    tag=tag,
                    model=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    attempts=attempt + 1,
                    latency_s=time.time() - start,
                    iteration=self.iteration,
                    estimated=estimated,
                )
            )
            return content

        raise last_exception  # unreachable, kept for type-checkers
