"""
Empirically verify that extra_body={"reasoning": {"enabled": False}} suppresses
reasoning tokens for deepseek/deepseek-v4-flash via OpenRouter.

Makes two calls: one with reasoning on, one off. Prints token breakdown for each.
"""
import asyncio
import os
import openai

MODEL = "deepseek/deepseek-v4-flash"
PROMPT = "What is 2+2? Answer in one word."

async def call(client, disable_reasoning: bool) -> dict:
    params = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 64,
    }
    if disable_reasoning:
        params["extra_body"] = {"reasoning": {"enabled": False}}

    resp = await client.chat.completions.create(**params)
    usage = resp.usage
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0 if details else 0
    return {
        "content": resp.choices[0].message.content,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "finish_reason": resp.choices[0].finish_reason,
    }

async def main():
    key = os.environ["OPENROUTER_API_KEY"]
    client = openai.AsyncOpenAI(
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
    )

    print(f"Model: {MODEL}\n")

    print("--- reasoning ON (default) ---")
    r_on = await call(client, disable_reasoning=False)
    print(f"  content:           {r_on['content']!r}")
    print(f"  completion_tokens: {r_on['completion_tokens']}")
    print(f"  reasoning_tokens:  {r_on['reasoning_tokens']}")
    print(f"  finish_reason:     {r_on['finish_reason']}")

    print("\n--- reasoning OFF (extra_body) ---")
    r_off = await call(client, disable_reasoning=True)
    print(f"  content:           {r_off['content']!r}")
    print(f"  completion_tokens: {r_off['completion_tokens']}")
    print(f"  reasoning_tokens:  {r_off['reasoning_tokens']}")
    print(f"  finish_reason:     {r_off['finish_reason']}")

    print("\n--- verdict ---")
    if r_on["reasoning_tokens"] == 0 and r_off["reasoning_tokens"] == 0:
        print("RESULT: model produces NO reasoning tokens regardless — disable is a no-op (non-reasoning model)")
    elif r_off["reasoning_tokens"] < r_on["reasoning_tokens"]:
        reduction = r_on["reasoning_tokens"] - r_off["reasoning_tokens"]
        print(f"RESULT: disable WORKS — reasoning tokens reduced by {reduction} "
              f"({r_on['reasoning_tokens']} → {r_off['reasoning_tokens']})")
    else:
        print(f"RESULT: disable had NO EFFECT — reasoning tokens: on={r_on['reasoning_tokens']}, off={r_off['reasoning_tokens']}")

asyncio.run(main())
