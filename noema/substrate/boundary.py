"""
Enforces the F_imm / F_mut role-structured layout boundary (Tree Substrate
Plan Phase A). F_imm — the entry point, I/O contract, and foundational
utilities — lives outside the `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END`
markers; F_mut, the strategy code evolution is meant to touch, lives inside.

Neither of openevolve's parse paths enforces this on their own: `apply_diff`
matches a SEARCH/REPLACE hunk anywhere in the full parent text, not just
inside the evolve block, and `parse_full_rewrite` accepts whatever code block
the LLM emits wholesale. This module is the post-parse check the controller
calls to close that gap: it restores F_imm byte-for-byte from the parent,
keeping only the evolve-block interior from the child, or rejects the
mutation outright if the block structure itself was destroyed.
"""

from typing import Optional

from openevolve.utils.code_utils import parse_evolve_blocks


def enforce_immutable_boundary(parent_code: str, child_code: str) -> Optional[str]:
    """
    Return child_code with F_imm (everything outside the evolve block)
    restored to be byte-identical to parent_code. A no-op if parent_code
    doesn't declare an evolve block at all (the role-structured layout is
    opt-in per program). Returns None if the parent declares exactly one
    evolve block but the child doesn't match it, since F_mut can't be
    safely isolated in that case (the mutation is rejected).
    """
    parent_blocks = parse_evolve_blocks(parent_code)
    if not parent_blocks:
        return child_code

    child_blocks = parse_evolve_blocks(child_code)
    if len(parent_blocks) != 1 or len(child_blocks) != 1:
        return None

    parent_lines = parent_code.split("\n")
    child_lines = child_code.split("\n")
    p_start, p_end, _ = parent_blocks[0]
    _, _, child_block_content = child_blocks[0]

    restored_lines = (
        parent_lines[: p_start + 1]
        + child_block_content.split("\n")
        + parent_lines[p_end:]
    )
    return "\n".join(restored_lines)
