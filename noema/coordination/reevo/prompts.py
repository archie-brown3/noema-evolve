"""Prompt and code-filter helpers for ReEvo short-term reflection.

BORROWED CODE — Short-term reflector templates and ``filter_code`` semantics,
ported from ReEvo (MIT). Source: https://github.com/ai4co/reevo
  prompts/common/system_reflector.txt
  prompts/common/user_reflector_st.txt
  utils/utils.py (``filter_code``)
  pinned at commit 6dce18257da5e11db2d138e417a2fffc5c72d05f

Golden copies live under ``noema/coordination/reevo/golden/`` (mirrored in
``tests/fixtures/reevo/``).  This module keeps that small donor-shaped surface
isolated from Noema's host mutation prompts.

NOEMA adaptations (not in donor short-term path):
  - ``extract_evolve_block`` applied before ``donor_filter_code`` (host scaffold).
  - Config keys ``domain_context`` / ``function_name`` map to donor
    ``problem_desc`` / ``func_name``.
  - ``donor_filter_code`` matches its keywords on a word boundary rather than
    with the donor's bare ``str.startswith`` (see that function's docstring).
"""

from __future__ import annotations

import re
from pathlib import Path

from noema.coordination.pe.prompts import extract_evolve_block

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts" / "common"

SYSTEM_REFLECTOR = (_GOLDEN_DIR / "system_reflector.txt").read_text()
USER_REFLECTOR_ST_TEMPLATE = (_GOLDEN_DIR / "user_reflector_st.txt").read_text()

_SKIP_KEYWORD_RE = re.compile(r"(?:def|import|from)\b")
_STOP_KEYWORD_RE = re.compile(r"return\b")


def donor_filter_code(code: str) -> str:
    """Apply ReEvo's intentionally column-zero-sensitive code filter.

    The donor tests each line with bare ``str.startswith``, so a column-zero
    binding whose name merely begins with a keyword (``default_size = 10``,
    ``from_index = 0``) is dropped from the snippet without warning.  Matching
    on a word boundary keeps the donor's intent of acting only on column-zero
    keywords while leaving such names intact.
    """
    filtered: list[str] = []
    for line in code.splitlines():
        if _SKIP_KEYWORD_RE.match(line):
            continue
        filtered.append(line)
        if _STOP_KEYWORD_RE.match(line):
            break
    return "\n".join(filtered)


def reflection_code(program_code: str) -> str:
    """Expose the evolvable body, then apply the donor filter."""
    return donor_filter_code(extract_evolve_block(program_code) or program_code)


def render_short_term_reflection_prompt(
    *,
    domain_context: str,
    function_name: str,
    func_desc: str = "",
    worse_code: str,
    better_code: str,
) -> str:
    """Render the pinned ReEvo short-term comparison with worse code first."""
    return USER_REFLECTOR_ST_TEMPLATE.format(
        func_name=function_name,
        problem_desc=domain_context,
        func_desc=func_desc,
        worse_code=worse_code,
        better_code=better_code,
    )
