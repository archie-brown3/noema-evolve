"""Prompt and code-filter helpers for ReEvo short-term reflection.

The wording below is the short-term reflector template available in the local
LLM4AD ReEvo implementation.  Task 0149 pins the authoritative upstream
ai4co/reevo source and requires its final golden-source check before completion.
This module keeps that small donor-shaped surface isolated from Noema's host
mutation prompts.
"""

from __future__ import annotations

from noema.coordination.pe.prompts import extract_evolve_block

SYSTEM_REFLECTOR = (
    "You are an expert in the domain of optimization heuristics. "
    "Your task is to give hints to design better heuristics."
)


def donor_filter_code(code: str) -> str:
    """Apply ReEvo's intentionally column-zero-sensitive code filter."""
    filtered: list[str] = []
    for line in code.splitlines():
        if line.startswith("def") or line.startswith("import") or line.startswith("from"):
            continue
        filtered.append(line)
        if line.startswith("return"):
            break
    return "\n".join(filtered)


def reflection_code(program_code: str) -> str:
    """Expose the evolvable body, then apply the donor filter."""
    return donor_filter_code(extract_evolve_block(program_code) or program_code)


def render_short_term_reflection_prompt(
    *,
    domain_context: str,
    function_name: str,
    worse_code: str,
    better_code: str,
) -> str:
    """Render the ReEvo short-term comparison with worse code first."""
    return (
        f"Below are two {function_name} functions for {domain_context}.\n"
        "You are provided with two code versions below, where the second version "
        "performs better than the first one.\n"
        "[Worse code]\n"
        f"{worse_code}\n"
        "[Better code]\n"
        f"{better_code}\n"
        "You respond with some hints for designing better heuristics, based on the "
        "two code versions and using less than 20 words."
    )
