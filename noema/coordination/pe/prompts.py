"""Prompt builders for Punctuated Equilibrium (task 0109).

Adapted from LEVI (https://github.com/ttanv/levi, MIT (c) 2025 Temoor Tanveer),
``levi/equilibrium/prompts.py`` — reworded for noema's single-program benchmarks
(the donor targets multi-component "bundle" artifacts).
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Tuple

_EVOLVE_BLOCK_RE = re.compile(
    r"# EVOLVE-BLOCK-START\n(.*?)\n# EVOLVE-BLOCK-END",
    re.DOTALL,
)


def extract_evolve_block(code: str) -> Optional[str]:
    """Return the content between EVOLVE-BLOCK markers, or None if not found."""
    m = _EVOLVE_BLOCK_RE.search(code)
    return m.group(1).strip() if m else None


def splice_evolve_block(scaffold: str, new_block: str) -> Optional[str]:
    """Replace the EVOLVE-BLOCK content in scaffold with new_block."""
    if not _EVOLVE_BLOCK_RE.search(scaffold):
        return None
    return _EVOLVE_BLOCK_RE.sub(
        f"# EVOLVE-BLOCK-START\n{new_block}\n# EVOLVE-BLOCK-END",
        scaffold,
    )


def paradigm_shift_prompt(domain_context: str, representatives: Sequence[Tuple[str, float]]) -> str:
    blocks = "\n\n".join(
        f"# Existing approach {i + 1} (score {score:.4f}):\n```python\n{code}\n```"
        for i, (code, score) in enumerate(representatives)
    )
    return (
        f"{domain_context}\n\n"
        f"Below are {len(representatives)} structurally diverse evolvable functions already found:\n\n"
        f"{blocks}\n\n"
        "Propose a FUNDAMENTALLY DIFFERENT approach — a paradigm shift, not an "
        "incremental edit of any solution above. Use a different algorithmic strategy. "
        "Return ONLY the evolvable function body (the content that goes between "
        "# EVOLVE-BLOCK-START and # EVOLVE-BLOCK-END) in a single ```python code block. "
        "Do NOT return a complete program."
    )


def variant_prompt(domain_context: str, base_code: str, base_score: float) -> str:
    return (
        f"{domain_context}\n\n"
        f"Base evolvable function (score {base_score:.4f}):\n```python\n{base_code}\n```\n\n"
        "Generate a distinct variation of this approach that explores nearby but "
        "different behaviour (different parameters, structure, or refinement). "
        "Return ONLY the evolvable function body in a single ```python code block. "
        "Do NOT return a complete program."
    )
