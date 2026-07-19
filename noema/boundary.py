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

import re
from typing import List, Optional

from openevolve.utils.code_utils import parse_evolve_blocks

_IMPORT_LINE = re.compile(r"^\s*(?:import\s+\S|from\s+\S+\s+import\s+)")


def _new_import_lines(parent_head: List[str], child_head: List[str]) -> List[str]:
    """Top-level import lines present in child_head but not parent_head, in
    child order, deduplicated. Line-based (not ast-based): matches this
    module's existing text-splicing style and needs no new dependency."""
    parent_imports = {line for line in parent_head if _IMPORT_LINE.match(line)}
    new_imports: List[str] = []
    for line in child_head:
        if _IMPORT_LINE.match(line) and line not in parent_imports and line not in new_imports:
            new_imports.append(line)
    return new_imports


def enforce_immutable_boundary(
    parent_code: str, child_code: str, *, merge_new_imports: bool = False
) -> Optional[str]:
    """
    Return child_code with F_imm (everything outside the evolve block)
    restored to be byte-identical to parent_code. A no-op if parent_code
    doesn't declare an evolve block at all (the role-structured layout is
    opt-in per program). Returns None if the parent declares exactly one
    evolve block but the child doesn't match it, since F_mut can't be
    safely isolated in that case (the mutation is rejected).

    merge_new_imports: when True, top-level import lines the child added to
    its preamble (before EVOLVE-BLOCK-START) that aren't already in the
    parent's preamble are kept, prepended to the restored F_imm head. For
    full-program rewrites (PES-faithful directive mode), the child's evolve-
    block strategy code may depend on a library the parent's F_imm never
    imported; without this, the import is silently stripped and the evolve
    block crashes with NameError at evaluation time. Entry point and helper
    functions are still restored byte-identical to the parent — only new
    import lines pass through.
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
    c_start, _, child_block_content = child_blocks[0]

    head = parent_lines[: p_start + 1]
    if merge_new_imports:
        head = _new_import_lines(head, child_lines[: c_start + 1]) + head

    restored_lines = head + child_block_content.split("\n") + parent_lines[p_end:]
    return "\n".join(restored_lines)
