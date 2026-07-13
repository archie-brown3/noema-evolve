"""
Indentation-tolerant SEARCH/REPLACE diff application.

Drop-in replacement for openevolve's exact-match ``apply_diff``. The pinned
openevolve (utils.code_utils) matches SEARCH blocks line-for-line, so when a
model (e.g. Qwen2.5-Coder-14B) strips leading indentation from the SEARCH
block the match fails and the parent is returned unchanged — silently
producing byte-identical "mutations". This falls back to lstrip matching and
re-indents the REPLACE block to the matched original's indentation.

Proven against real PES run fixtures in tests/test_apply_diff.py.
"""

import re
from typing import List


def apply_diff_lenient(
    original_code: str,
    diff_text: str,
    diff_pattern: str = r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE",
) -> str:
    """
    Apply SEARCH/REPLACE diffs with indentation-aware matching.

    If the exact SEARCH block cannot be found, falls back to comparing
    lines with leading whitespace stripped.  When a stripped match is
    found, the REPLACE block is re-indented to match the original
    block's indentation level.
    """
    original_lines = original_code.split("\n")
    result_lines = original_lines.copy()

    diff_blocks = re.findall(diff_pattern, diff_text, re.DOTALL)
    blocks = [(m[0].rstrip(), m[1].rstrip()) for m in diff_blocks]

    for search_text, replace_text in blocks:
        search_lines = search_text.split("\n")
        replace_lines = replace_text.split("\n")

        matched = False
        # 1. Try exact match (existing behaviour)
        for i in range(len(result_lines) - len(search_lines) + 1):
            if result_lines[i : i + len(search_lines)] == search_lines:
                result_lines[i : i + len(search_lines)] = replace_lines
                matched = True
                break

        if matched:
            continue

        # 2. Fall back to lstrip matching
        orig_stripped = [line.lstrip() for line in result_lines]
        search_stripped = [line.lstrip() for line in search_lines]

        for i in range(len(orig_stripped) - len(search_stripped) + 1):
            if orig_stripped[i : i + len(search_stripped)] == search_stripped:
                # Determine indentation from the matched original block
                indent = ""
                for line in result_lines[i : i + len(search_lines)]:
                    if line.strip():
                        indent = line[: len(line) - len(line.lstrip())]
                        break

                # Re-indent the replace lines
                reindented: List[str] = []
                for rline in replace_lines:
                    if rline.strip():
                        reindented.append(indent + rline)
                    else:
                        reindented.append(rline)

                result_lines[i : i + len(search_lines)] = reindented
                matched = True
                break

        # 3. If neither matched, leave code unchanged

    return "\n".join(result_lines)
