"""Inner-session submit parsers for deep coordination CLI sessions (task 0175)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ADVICE_FILENAME = "ADVICE.json"


def _load_advice(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open() as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def coordination_response(work_dir: Path | str, tag: str) -> Optional[str]:
    """Read ADVICE.json and map to the str shape coordination modules expect."""
    path = Path(work_dir) / ADVICE_FILENAME
    if not path.is_file():
        return None
    data = _load_advice(path)
    if data is None:
        return None
    response = data.get("response")
    if response:
        return str(response)
    if tag == "hifo.extract_insights":
        return _hifo_extract_response(data)
    prompt_block = data.get("prompt_block")
    return str(prompt_block) if prompt_block else None


def _hifo_extract_response(data: Dict[str, Any]) -> Optional[str]:
    attribution = data.get("attribution") or {}
    insights = attribution.get("insights")
    if isinstance(insights, list) and insights:
        return "\n".join(f"- {item}" for item in insights if item)
    prompt_block = str(data.get("prompt_block") or "")
    bullet_lines = [
        line.strip() for line in prompt_block.splitlines() if line.strip().startswith("-")
    ]
    if bullet_lines:
        return "\n".join(bullet_lines)
    return prompt_block or None
