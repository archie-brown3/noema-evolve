"""Golden-source fidelity for the ReEvo short-term reflection arm (task 0149).

The donor is the MIT ``ai4co/reevo`` repository, pinned at commit
``6dce18257da5e11db2d138e417a2fffc5c72d05f``.  Tests pin the loaded template
constants and a rendered wire prompt.  Comparator selection is a Noema host
adaptation and is covered separately in ``test_reevo_module.py``.
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from noema.coordination.reevo.prompts import (
    SYSTEM_REFLECTOR,
    USER_REFLECTOR_ST_TEMPLATE,
    donor_filter_code,
    render_short_term_reflection_prompt,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "reevo" / "prompts" / "common"


class TestReEvoGoldenSourceFidelity(unittest.TestCase):
    def test_fixture_files_match_package_golden(self):
        for name in ("system_reflector.txt", "user_reflector_st.txt"):
            fixture_text = (_FIXTURE_DIR / name).read_text()
            package_text = (
                Path(__file__).resolve().parents[1]
                / "noema"
                / "coordination"
                / "reevo"
                / "golden"
                / "prompts"
                / "common"
                / name
            ).read_text()
            self.assertEqual(fixture_text, package_text)

    def test_constants_byte_pinned(self):
        expected = {
            SYSTEM_REFLECTOR: (
                "6bfb8c7972f4c27c73a8c88d9f0b76161ba6e9beec8d31d6322772e3ae95703b"
            ),
            USER_REFLECTOR_ST_TEMPLATE: (
                "9643353f5935b3b677ba7566d5e20c686668d17d20b966a1d072113c6a07e2e2"
            ),
        }
        for constant, sha in expected.items():
            self.assertEqual(hashlib.sha256(constant.encode()).hexdigest(), sha)

    def test_rendered_prompt_byte_pinned(self):
        rendered = render_short_term_reflection_prompt(
            domain_context="maximize packing quality",
            function_name="heuristic",
            func_desc="",
            worse_code="    return 1",
            better_code="    return 2",
        )
        self.assertEqual(
            hashlib.sha256(rendered.encode()).hexdigest(),
            "207122445b235dadd5272191be3d1127c42eb67093485ab03165016e4b50826b",
        )

    def test_func_desc_line_preserved_when_nonempty(self):
        rendered = render_short_term_reflection_prompt(
            domain_context="a problem",
            function_name="h",
            func_desc="Minimize total cost.",
            worse_code="WORSE",
            better_code="BETTER",
        )
        self.assertIn("Minimize total cost.", rendered)
        self.assertLess(rendered.index("a problem"), rendered.index("Minimize total cost."))

    def test_donor_filter_matches_upstream_filter_code(self):
        code = (
            "import x\nfrom y import z\ndef f():\n"
            "    value = 1\nreturn value\nignored = 2"
        )
        self.assertEqual(donor_filter_code(code), "    value = 1\nreturn value")

    def test_donor_filter_deviates_on_keyword_prefixed_identifiers(self):
        """Documented deviation: the donor's bare ``startswith`` drops these.

        Upstream ``filter_code`` would discard every line below because each
        one starts with a filter keyword as a bare string prefix.  Noema
        matches on a word boundary so column-zero bindings survive; the
        donor's column-zero sensitivity itself is unchanged.
        """
        code = "default_size = 10\nimport_count = 1\nfrom_index = 0"
        self.assertEqual(donor_filter_code(code), code)


if __name__ == "__main__":
    unittest.main()
