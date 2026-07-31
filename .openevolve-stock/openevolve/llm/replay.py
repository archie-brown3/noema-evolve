"""Deterministic, zero-network LLM used by task-0132 controller replay tests."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from openevolve.llm.base import LLMInterface


class ReplayLLM(LLMInterface):
    """Return a fixture response selected by the controller's mutation index."""

    def __init__(self, config):
        self.model = config.name or "task-0132-replay"
        self.mutation_index = 0
        fixture_path = os.environ.get("OPENEVOLVE_REPLAY_FIXTURE")
        if not fixture_path:
            raise RuntimeError("OPENEVOLVE_REPLAY_FIXTURE is required")
        payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        self.responses = {
            int(key): value for key, value in payload["responses"].items()
        }

    async def generate(self, prompt: str, **kwargs) -> str:
        return await self._response()

    async def generate_with_context(
        self, system_message: str, messages: List[Dict[str, str]], **kwargs
    ) -> str:
        return await self._response()

    async def _response(self) -> str:
        item = self.responses[self.mutation_index]
        delay = float(item.get("delay_s", 0.0))
        if delay:
            await asyncio.sleep(delay)
        if item.get("error"):
            raise RuntimeError(str(item["error"]))
        return str(item["content"])


def create_replay_llm(config) -> ReplayLLM:
    """Pickle-safe initializer carried through ProcessParallelController."""

    return ReplayLLM(config)
