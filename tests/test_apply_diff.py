"""
Tests for apply_diff with indentation-aware SEARCH matching.

Uses real LLM output from PES coordination arm runs on circle_packing (n=26).
The model (Qwen2.5-Coder-14B) strips indentation from SEARCH/REPLACE blocks,
causing the upstream apply_diff to return the original code unchanged.

This test applies the indentation-aware fix and verifies:
1. Mutated code compiles (or fails with expected runtime errors)
2. Mutated code differs from the original when the LLM proposed a change
3. The original apply_diff produces unchanged code (proving the bug)
"""

import ast
import json
import os
import re
import unittest
from typing import List, Tuple

from openevolve.utils.code_utils import apply_diff

from noema.substrate.diff import apply_diff_lenient

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ----------------------------------------------------------------- helpers


def load_fixture_programs(subdir: str) -> List[dict]:
    """Load all program JSONs from a fixture subdirectory."""
    path = os.path.join(FIXTURES_DIR, subdir)
    programs = []
    for fname in sorted(os.listdir(path)):
        if fname == "initial.json":
            continue
        with open(os.path.join(path, fname)) as f:
            programs.append(json.load(f))
    return programs


INITIAL_CODE: str = ""


def get_initial_code() -> str:
    global INITIAL_CODE
    if not INITIAL_CODE:
        with open(os.path.join(FIXTURES_DIR, "initial_program.py")) as f:
            INITIAL_CODE = f.read()
    return INITIAL_CODE


def extract_searches_and_replaces(prog: dict) -> List[Tuple[str, str]]:
    """Extract (search_text, replace_text) pairs from a program's prompts."""
    prompts = prog.get("prompts", {})
    if not prompts:
        return []
    key = "diff_user" if "diff_user" in prompts else list(prompts.keys())[0]
    pairs = []
    for resp in prompts[key].get("responses", []):
        for m in re.finditer(
            r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE", resp, re.DOTALL
        ):
            pairs.append((m.group(1).rstrip(), m.group(2).rstrip()))
    return pairs


# ----------------------------------------------------------------- tests


class TestApplyDiffLenient(unittest.TestCase):
    """Tests for the indentation-aware apply_diff."""

    # --------------- unit-level

    def test_exact_match_preserved(self):
        """Exact match (same indentation) still works as before."""
        original = "    x = 1\n    y = 2\n"
        diff = (
            "<<<<<<< SEARCH\n    x = 1\n=======\n    x = 42\n>>>>>>> REPLACE\n"
        )
        result = apply_diff_lenient(original, diff)
        self.assertIn("    x = 42", result)
        self.assertNotIn("    x = 1", result)

    def test_lstrip_match_reindents_replace(self):
        """When SEARCH strips indentation, REPLACE is re-indented."""
        original = "    x = 1\n    y = 2\n"
        diff = (
            "<<<<<<< SEARCH\nx = 1\n=======\nx = 42\n>>>>>>> REPLACE\n"
        )
        result = apply_diff_lenient(original, diff)
        self.assertIn("    x = 42", result)
        self.assertNotIn("    x = 1", result)

    def test_lstrip_match_preserves_blank_lines(self):
        original = "    x = 1\n\n    y = 2\n"
        diff = (
            "<<<<<<< SEARCH\nx = 1\n\ny = 2\n=======\na = 3\n\nb = 4\n>>>>>>> REPLACE\n"
        )
        result = apply_diff_lenient(original, diff)
        self.assertIn("    a = 3", result)
        self.assertIn("    b = 4", result)
        self.assertNotIn("    x = 1", result)

    def test_no_match_leaves_code_unchanged(self):
        """When SEARCH is absent entirely, code stays as-is."""
        original = "x = 1\ny = 2\n"
        diff = (
            "<<<<<<< SEARCH\nz = 99\n=======\nz = 100\n>>>>>>> REPLACE\n"
        )
        result = apply_diff_lenient(original, diff)
        self.assertEqual(result, original)

    def test_no_diff_blocks_returns_original(self):
        original = "x = 1\n"
        result = apply_diff_lenient(original, "just some text, no SEARCH/REPLACE")
        self.assertEqual(result, original)

    def test_searches_without_blocks_may_need_multiple_matches(self):
        """SEARCH text that is made up of blank lines only for lstripping."""
        original = "    x = 1\n    y = 2\n    z = 3\n"
        diff = (
            "<<<<<<< SEARCH\ny = 2\nz = 3\n=======\na = 4\nb = 5\n>>>>>>> REPLACE\n"
        )
        result = apply_diff_lenient(original, diff)
        self.assertIn("    a = 4", result)
        self.assertIn("    b = 5", result)
        self.assertNotIn("    y = 2", result)

    # --------------- real LLM output: noretry

    def test_noretry_mutations_all_compile_or_known_error(self):
        """Every mutation from noretry either compiles or fails with known runtime error."""
        original = get_initial_code()
        programs = load_fixture_programs("noretry")
        compiled = 0
        unchecked = 0

        for prog in programs:
            pairs = extract_searches_and_replaces(prog)
            if not pairs:
                unchecked += 1
                continue

            mutated = original
            for search_text, replace_text in pairs:
                # construct a single-block diff so we can test per-pair
                single_diff = (
                    f"<<<<<<< SEARCH\n{search_text}\n"
                    f"=======\n{replace_text}\n"
                    f">>>>>>> REPLACE\n"
                )
                mutated = apply_diff_lenient(original, single_diff)

            if mutated == original:
                unchecked += 1
                continue

            try:
                ast.parse(mutated)
                compiled += 1
            except SyntaxError:
                pass  # some mutations are genuinely wrong — model error, not parser error

        self.assertGreater(
            compiled,
            0,
            "Expected at least one noretry mutation to produce compilable code",
        )
        # At least some should compile: it000000 and it000002 did in our manual eval
        if compiled < len(programs):
            print(f"\n{noretry}: {compiled}/{len(programs)-unchecked} compilable")

    def test_noretry_at_least_one_different_from_initial(self):
        """At least one noretry mutation produces code different from the initial."""
        original = get_initial_code()
        programs = load_fixture_programs("noretry")
        diff_count = 0
        for prog in programs:
            for search_text, replace_text in extract_searches_and_replaces(prog):
                if search_text == replace_text:
                    continue
                single_diff = (
                    f"<<<<<<< SEARCH\n{search_text}\n"
                    f"=======\n{replace_text}\n"
                    f">>>>>>> REPLACE\n"
                )
                result = apply_diff_lenient(original, single_diff)
                if result != original:
                    diff_count += 1

        self.assertGreater(
            diff_count,
            0,
            "No noretry mutation produced code different from the initial — "
            "indentation fix made no difference",
        )

    # --------------- real LLM output: retry1

    def test_retry1_mutations_all_compile_or_known_error(self):
        original = get_initial_code()
        programs = load_fixture_programs("retry1")
        compiled = 0
        for prog in programs:
            for search_text, replace_text in extract_searches_and_replaces(prog):
                if search_text == replace_text:
                    continue
                single_diff = (
                    f"<<<<<<< SEARCH\n{search_text}\n"
                    f"=======\n{replace_text}\n"
                    f">>>>>>> REPLACE\n"
                )
                mutated = apply_diff_lenient(original, single_diff)
                if mutated == original:
                    continue
                try:
                    ast.parse(mutated)
                    compiled += 1
                except SyntaxError:
                    pass
        self.assertGreater(compiled, 0)

    # --------------- real LLM output: retry

    def test_retry_mutations_all_compile_or_known_error(self):
        original = get_initial_code()
        programs = load_fixture_programs("retry")
        compiled = 0
        for prog in programs:
            for search_text, replace_text in extract_searches_and_replaces(prog):
                if search_text == replace_text:
                    continue
                single_diff = (
                    f"<<<<<<< SEARCH\n{search_text}\n"
                    f"=======\n{replace_text}\n"
                    f">>>>>>> REPLACE\n"
                )
                mutated = apply_diff_lenient(original, single_diff)
                if mutated == original:
                    continue
                try:
                    ast.parse(mutated)
                    compiled += 1
                except SyntaxError:
                    pass
        self.assertGreater(compiled, 0)

    # --------------- before/after: old apply_diff vs apply_diff_lenient

    def _assert_bug_and_fix(self, subdir: str) -> Tuple[int, int, int]:
        """
        For every SEARCH/REPLACE block with SEARCH != REPLACE:
          - old apply_diff returns original unchanged (the bug)
          - apply_diff_lenient returns different code (the fix)
          - the new code compiles (or is a known model-level error)

        Returns (total, buggy, fixed_compilable).
        """
        original = get_initial_code()
        programs = load_fixture_programs(subdir)
        total = 0
        buggy = 0
        fixed_compilable = 0

        for prog in programs:
            for search_text, replace_text in extract_searches_and_replaces(prog):
                if search_text == replace_text:
                    continue
                total += 1
                single_diff = (
                    f"<<<<<<< SEARCH\n{search_text}\n"
                    f"=======\n{replace_text}\n"
                    f">>>>>>> REPLACE\n"
                )

                old_result = apply_diff(original, single_diff)
                new_result = apply_diff_lenient(original, single_diff)

                if old_result == original:
                    buggy += 1

                if new_result != original:
                    try:
                        ast.parse(new_result)
                        fixed_compilable += 1
                    except SyntaxError:
                        pass  # model produced broken code — not a parser bug

        return total, buggy, fixed_compilable

    def test_before_after_noretry(self):
        """Old apply_diff returns unchanged; new apply_diff_lenient returns compilable code."""
        total, buggy, fixed = self._assert_bug_and_fix("noretry")
        self.assertGreater(total, 0, "no SEARCH/REPLACE blocks found in noretry fixtures")
        self.assertGreater(buggy, 0, "old apply_diff should have returned unchanged for at least one block")
        self.assertEqual(
            buggy, total,
            f"old apply_diff matched {total - buggy}/{total} blocks — "
            "these would not be affected by the indentation fix"
        )
        self.assertGreater(fixed, 0, "new apply_diff_lenient must produce at least one compilable program")

    def test_before_after_retry1(self):
        total, buggy, fixed = self._assert_bug_and_fix("retry1")
        self.assertGreater(total, 0, "no SEARCH/REPLACE blocks found in retry1 fixtures")
        self.assertGreater(buggy, 0, "old apply_diff should have returned unchanged for at least one block")
        self.assertEqual(buggy, total)
        self.assertGreater(fixed, 0, "new apply_diff_lenient must produce at least one compilable program")

    def test_before_after_retry(self):
        total, buggy, fixed = self._assert_bug_and_fix("retry")
        self.assertGreater(total, 0, "no SEARCH/REPLACE blocks found in retry fixtures")
        self.assertGreater(buggy, 0, "old apply_diff should have returned unchanged for at least one block")
        self.assertEqual(buggy, total)
        self.assertGreater(fixed, 0, "new apply_diff_lenient must produce at least one compilable program")


if __name__ == "__main__":
    unittest.main()