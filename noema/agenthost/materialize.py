"""Turn a mutation response / deliverable into child code (controller parse seams)."""

from __future__ import annotations

from typing import Optional

from openevolve.utils.code_utils import extract_diffs, parse_full_rewrite

from noema.evolution.diff import apply_diff_lenient as apply_diff


def materialize_child_code(
    text: str,
    parent_code: str,
    *,
    parse_mode: str,
    language: str,
    diff_pattern: str,
) -> Optional[str]:
    """Mirror ``NoemaController._parse_response`` for file or chat-shaped output.

    Coding CLIs usually write a complete program to the deliverable. If the
    selected operator is diff-based and the text still contains SEARCH/REPLACE
    blocks, apply them to the parent with the same helpers the controller uses.
    """
    if not text or not str(text).strip():
        return None

    if parse_mode == "diff":
        diff_blocks = extract_diffs(text, diff_pattern)
        if diff_blocks:
            return apply_diff(parent_code, text, diff_pattern)
        # Deliverable already looks like a full program (CLI edited in place).
        rewritten = parse_full_rewrite(text, language)
        return rewritten or text

    rewritten = parse_full_rewrite(text, language)
    return rewritten or text
