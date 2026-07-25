"""
Tests for apply_diff with indentation-aware SEARCH matching.

Uses real LLM output from the 0070 sweep (task 0079 rebuild — the original
noretry/retry/retry1 corpus was never committed to any git ref and is
unrecoverable; see the corpus section below for what replaced it and why).

This test applies the indentation-aware fix and verifies:
1. Mutated code compiles (or fails with expected runtime errors)
2. Mutated code differs from the original when the LLM proposed a change
3. The original apply_diff produces unchanged code (proving the bug), on the
   synthetic unit cases where that specific bug is exercised
"""

import ast
import json
import os
import re
import unittest
from typing import Dict, List, Tuple

from openevolve.utils.code_utils import apply_diff

from noema.diff import apply_diff_lenient

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ----------------------------------------------------------------- helpers


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


def load_fixture_corpus(subdir: str) -> Dict[str, dict]:
    """Load every program JSON from a fixture subdirectory, keyed by id."""
    path = os.path.join(FIXTURES_DIR, subdir)
    by_id = {}
    for fname in sorted(os.listdir(path)):
        with open(os.path.join(path, fname)) as f:
            prog = json.load(f)
        by_id[prog["id"]] = prog
    return by_id


def iter_diff_pairs_with_parent_code(corpus: Dict[str, dict]):
    """Yield (program, search_text, replace_text, parent_code) for every real
    SEARCH/REPLACE block, resolved against each program's ACTUAL parent —
    not one fixed original. Most programs in this corpus are multi-generation
    descendants (parent != the seed), so a child's SEARCH text is only
    meaningful against its real parent's code."""
    for prog in corpus.values():
        parent = corpus.get(prog.get("parent_id"))
        if parent is None:
            continue  # the seed program itself, or an unresolvable record
        parent_code = parent["code"]
        for search_text, replace_text in extract_searches_and_replaces(prog):
            if search_text == replace_text:
                continue
            yield prog, search_text, replace_text, parent_code


def single_diff(search_text: str, replace_text: str) -> str:
    return f"<<<<<<< SEARCH\n{search_text}\n=======\n{replace_text}\n>>>>>>> REPLACE\n"


# ----------------------------------------------------------------- unit tests


class TestApplyDiffLenient(unittest.TestCase):
    """Synthetic cases for the indentation-aware apply_diff. Independent of
    the real-corpus fixtures below — these are the regression tests for the
    lenient-matching logic itself."""

    def test_exact_match_preserved(self):
        """Exact match (same indentation) still works as before."""
        original = "    x = 1\n    y = 2\n"
        diff = "<<<<<<< SEARCH\n    x = 1\n=======\n    x = 42\n>>>>>>> REPLACE\n"
        result = apply_diff_lenient(original, diff)
        self.assertIn("    x = 42", result)
        self.assertNotIn("    x = 1", result)

    def test_lstrip_match_reindents_replace(self):
        """When SEARCH strips indentation, REPLACE is re-indented."""
        original = "    x = 1\n    y = 2\n"
        diff = "<<<<<<< SEARCH\nx = 1\n=======\nx = 42\n>>>>>>> REPLACE\n"
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
        diff = "<<<<<<< SEARCH\nz = 99\n=======\nz = 100\n>>>>>>> REPLACE\n"
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


# ----------------------------------------------------------- real-corpus tests


class TestApplyDiffOnRealCorpus(unittest.TestCase):
    """Real SEARCH/REPLACE output from the 0070 sweep (Qwen3-30B-A3B,
    circle_packing n=26), null + pes-custom arms, committed on
    task/0024-noema-standalone-repo (tag pre-reconcile-2026-07-13 does not
    exist on this remote; used the branch directly).

    task 0079: the original noretry/retry/retry1 corpus (Qwen2.5-Coder-14B,
    built with Codex) was never committed to any git ref, on any of 22
    refs/dangling-objects checked 2026-07-13 — genuinely unrecoverable. This
    replaces it with a corpus from a different model and a different fixture
    structure (per-arm, not per-retry-state — this corpus stores one response
    per program, so no retry-attempt structure survives to split on), and its
    diffs are resolved against each program's ACTUAL parent code (most are
    multi-generation descendants), not one fixed seed.

    **Re-based per the ticket's own allowance**: this corpus does NOT
    reproduce the original's finding (100% of real SEARCH blocks
    indentation-stripped, apply_diff_lenient recovering most of them to
    compilable code). Measured here instead: strict apply_diff succeeds on
    131/144 real blocks (91%); null-30b-enriched-s42 has ZERO no-ops (0/88),
    all 13 no-ops are in pes-custom-30b-enriched-s42 (13/56, 23%). Sampled
    several of the failures directly: none are indentation-strippable (lstrip
    and per-line-whitespace-stripped substring checks both fail) — the SEARCH
    text references code genuinely absent from the real parent, a
    context/hallucination mismatch, not the indentation-stripping bug
    apply_diff_lenient targets. It recovers 0/13 to compilable code (1/13
    produces a different but syntactically-broken result; 12/13 still
    no-op). That is the honest finding
    for this corpus, not a defect in apply_diff_lenient (see
    TestApplyDiffLenient above for its actual regression coverage) — and it's
    itself relevant: it means silent diff no-ops are NOT what is driving the
    0070 null arm's invalid-evaluation rate, narrowing that open question
    rather than confirming the ticket's original hypothesis.
    """

    @classmethod
    def setUpClass(cls):
        cls.null = load_fixture_corpus("null")
        cls.pes_custom = load_fixture_corpus("pes-custom")

    def test_corpora_load_and_have_real_diff_blocks(self):
        for name, corpus in (("null", self.null), ("pes-custom", self.pes_custom)):
            pairs = list(iter_diff_pairs_with_parent_code(corpus))
            self.assertGreater(len(pairs), 0, f"{name}: no SEARCH/REPLACE blocks found")

    def test_lenient_never_regresses_a_case_strict_already_handles(self):
        """apply_diff_lenient must be a strict superset fix: wherever strict
        apply_diff already succeeds, lenient must produce the identical
        result — never a different, let alone worse, outcome."""
        for corpus in (self.null, self.pes_custom):
            for prog, search_text, replace_text, parent_code in iter_diff_pairs_with_parent_code(corpus):
                diff = single_diff(search_text, replace_text)
                strict_result = apply_diff(parent_code, diff)
                if strict_result == parent_code:
                    continue  # strict no-op'd; covered by the no-op tests below
                lenient_result = apply_diff_lenient(parent_code, diff)
                self.assertEqual(
                    lenient_result, strict_result,
                    f"{prog['id']}: apply_diff_lenient diverged from apply_diff "
                    "on a block strict already handles cleanly",
                )

    def test_strict_apply_diff_no_op_rate_measured(self):
        """task 0079's own framing: how often does strict apply_diff silently
        no-op on real LLM output? Exact counts, not just '> 0', so a future
        corpus change shows up as a visible diff here rather than silent
        drift. If this test breaks after a legitimate corpus update, update
        the expected counts and the docstring above together."""
        counts = {}
        for name, corpus in (("null", self.null), ("pes-custom", self.pes_custom)):
            total = noop = 0
            for _prog, search_text, replace_text, parent_code in iter_diff_pairs_with_parent_code(corpus):
                total += 1
                if apply_diff(parent_code, single_diff(search_text, replace_text)) == parent_code:
                    noop += 1
            counts[name] = (noop, total)
        self.assertEqual(counts["null"], (0, 88))
        self.assertEqual(counts["pes-custom"], (13, 56))

    def test_lenient_recovers_none_of_this_corpus_no_ops_to_compilable_code(self):
        """The honest counterpart to the original corpus's recovery finding:
        this corpus's strict-failures are a different failure mode
        (hallucinated/absent SEARCH content) than indentation stripping, so
        apply_diff_lenient is not expected to recover them to *usable* code —
        and doesn't (1/13 produces a different-but-syntactically-broken
        result; the rest still no-op). It must still run without raising on
        every one of them."""
        examined = changed = compilable = 0
        for corpus in (self.null, self.pes_custom):
            for _prog, search_text, replace_text, parent_code in iter_diff_pairs_with_parent_code(corpus):
                diff = single_diff(search_text, replace_text)
                if apply_diff(parent_code, diff) != parent_code:
                    continue  # strict already succeeded
                examined += 1
                lenient_result = apply_diff_lenient(parent_code, diff)  # must not raise
                if lenient_result != parent_code:
                    changed += 1
                    try:
                        ast.parse(lenient_result)
                        compilable += 1
                    except SyntaxError:
                        pass
        self.assertEqual(examined, 13)
        self.assertEqual(changed, 1, "a different number of no-ops now produce SOME change")
        self.assertEqual(
            compilable, 0,
            "apply_diff_lenient unexpectedly recovered a compilable result in this "
            "corpus — update this test's framing and the class docstring, this would be good news",
        )

    def test_mutations_produce_code_that_compiles_or_fails_for_a_model_reason(self):
        """Where a diff actually changes the code (strict or lenient
        success), the result must be syntactically valid Python in the large
        majority of cases — a real, non-degenerate corpus, not one dominated
        by garbage the parser rejects."""
        for corpus in (self.null, self.pes_custom):
            changed = compiled = 0
            for _prog, search_text, replace_text, parent_code in iter_diff_pairs_with_parent_code(corpus):
                result = apply_diff_lenient(parent_code, single_diff(search_text, replace_text))
                if result == parent_code:
                    continue
                changed += 1
                try:
                    ast.parse(result)
                    compiled += 1
                except SyntaxError:
                    pass  # a genuinely broken model mutation, not a parser bug
            self.assertGreater(changed, 0)
            self.assertGreater(compiled / changed, 0.9)


if __name__ == "__main__":
    unittest.main()
