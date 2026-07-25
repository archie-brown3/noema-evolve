"""Prompt builders for Punctuated Equilibrium (task 0109).

Adapted from LEVI (https://github.com/ttanv/levi, MIT (c) 2025 Temoor Tanveer),
``levi/equilibrium/prompts.py`` — reworded for noema's single-program benchmarks
(the donor targets multi-component "bundle" artifacts).
"""

from __future__ import annotations

from typing import Sequence, Tuple


def paradigm_shift_prompt(domain_context: str, representatives: Sequence[Tuple[str, float]]) -> str:
    blocks = "\n\n".join(
        f"# Existing approach {i + 1} (score {score:.4f}):\n```python\n{code}\n```"
        for i, (code, score) in enumerate(representatives)
    )
    return (
        f"{domain_context}\n\n"
        f"Below are {len(representatives)} structurally diverse solutions already found:\n\n"
        f"{blocks}\n\n"
        "Propose a FUNDAMENTALLY DIFFERENT approach — a paradigm shift, not an "
        "incremental edit of any solution above. Use a different algorithmic strategy. "
        "Return ONLY one complete, self-contained Python program in a single ```python "
        "code block."
    )


def variant_prompt(domain_context: str, base_code: str, base_score: float) -> str:
    return (
        f"{domain_context}\n\n"
        f"Base solution (score {base_score:.4f}):\n```python\n{base_code}\n```\n\n"
        "Generate a distinct variation of this approach that explores nearby but "
        "different behaviour (different parameters, structure, or refinement). "
        "Return ONLY one complete Python program in a single ```python code block."
    )
