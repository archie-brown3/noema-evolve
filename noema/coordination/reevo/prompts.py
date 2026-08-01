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
"""

from __future__ import annotations

from pathlib import Path

from noema.coordination.pe.prompts import extract_evolve_block

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "prompts" / "common"

SYSTEM_REFLECTOR = (_GOLDEN_DIR / "system_reflector.txt").read_text()
USER_REFLECTOR_ST_TEMPLATE = (_GOLDEN_DIR / "user_reflector_st.txt").read_text()


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
