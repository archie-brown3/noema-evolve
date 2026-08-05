"""0188 Stage 4: characterization of upstream ``parse_full_rewrite`` and
``parse_evolve_blocks``.

Noema-authored characterization tests (not pin-regression): they describe what
the *installed upstream* parsers do today, so that any change to them — an
upstream bump, a local patch — shows up as a failure here rather than as a
silently different child program entering the population.

Grounding: vault note "0188 OpenEvolve Fidelity Spec §6 — Scope Table —
2026-08-05" §2.2–§2.3 and rows #10/#11, against
``openevolve/utils/code_utils.py`` at pin ``80945ed`` (``parse_evolve_blocks``
at ``:9-37``, ``parse_full_rewrite`` at ``:95-120``).

Upstream test coverage of both functions is **zero** — ``grep -rn
"parse_full_rewrite\\|parse_evolve_blocks" tests/upstream/openevolve/`` exits 1
with no matches across all 44 donor test files. (The one hit elsewhere under
``tests/upstream/`` is ``loongflow/.../test_parse.py``, which imports a
same-named function from a different project.) That absence is why these are
written rather than vendored.

Consumption under characterization — spec §2.2/§2.3, re-verified on this
branch: ``parse_full_rewrite`` is **4 calls / 3 files**
(``agenthost/materialize.py:37,40``, ``evolution/iteration_runner.py:881``,
``coordination/pe/module.py:118``); ``parse_evolve_blocks`` is **2 calls /
1 file** (``evolution/boundary.py:57,61``).

## PIN, DON'T FIX

Several behaviours pinned below are suspected bugs. They are pinned exactly as
they behave, with no edit under ``noema/`` and none to upstream. Flagged for the
orchestrator in the stage log, ranked by blast radius:

1. A ``# EVOLVE-BLOCK-START`` marker appearing *anywhere* in a line — including
   inside a string literal — opens a block (``:27``), and a second open silently
   discards everything accumulated so far (``:28-30``). A child whose evolve
   block legitimately contains the marker text therefore reaches
   ``enforce_immutable_boundary`` as a **one-block program whose body has been
   truncated to whatever followed the last marker mention** — and is *accepted*,
   not rejected: the model's work is deleted and the resulting failure is
   attributed to the model.
2. ``parse_full_rewrite``'s any-fence fallback (``:113-117``) captures the
   language tag as part of the code. A response fenced ```` ```cpp ````,
   ```` ```py ```` or ```` ```Python ```` — anything that is not a byte-exact
   ``language`` match followed by ``\\n`` — yields code whose first line is the
   tag, i.e. a guaranteed ``SyntaxError`` at evaluation. All 4 call sites.
3. ``parse_full_rewrite`` never returns ``None`` despite its ``Optional[str]``
   annotation (``:120`` returns the whole response). Prose with no fence at all
   becomes "child code"; the ``if not new_code`` guards at
   ``iteration_runner.py:882`` and ``pe/module.py:119`` only fire on the empty
   string.
4. A parent whose ``EVOLVE-BLOCK-START`` has no matching ``END`` parses to
   ``[]`` (``:31`` requires ``in_block``, and an unterminated block is never
   appended), indistinguishable from a parent that declares no block at all.
   ``boundary.py:58-59`` then returns the child **unfiltered** — F_imm is not
   restored. Trigger is a malformed *seed* program: every enforced mutation
   re-appends the parent's END marker (``boundary.py:74``), so the corpus cannot
   drift into this state on its own.
5. ``language`` is interpolated into the regex unescaped (``:106``), so a
   metacharacter-bearing value raises ``re.error`` out of the parser.
   ``noema/config.py:164`` types it as a bare ``str`` with no validation.

One further hazard is pinned in ``TestNoemaCallSites``: the agent-host transport
re-fences child code before handing it back to ``IterationRunner``
(``agenthost/mutation_transport.py:70-71``) and the non-greedy fence regex
truncates any program containing a triple backtick. That one is **latent** — a
short-circuit at ``iteration_runner.py:339-343`` currently prefers the stashed
child over re-parsing — and is labelled as such at its test.
"""

from __future__ import annotations

import ast
import re
import unittest
from collections import Counter
from pathlib import Path

from openevolve.utils.code_utils import parse_evolve_blocks, parse_full_rewrite

from noema.agenthost.materialize import materialize_child_code
from noema.evolution.boundary import enforce_immutable_boundary

FENCE = "```"


class TestParseFullRewrite(unittest.TestCase):
    """``[up] openevolve/utils/code_utils.py:95-120``."""

    def test_the_language_fence_wins_over_an_earlier_foreign_fence(self):
        """``:106-110`` scans the whole response for ``language`` first.

        Discriminating: a single-pass "first fence wins" parser returns
        ``a=1``. Order in the response does not decide; the tag does.
        """
        response = f"{FENCE}js\na=1\n{FENCE}\nthen the real one:\n{FENCE}python\nb=2\n{FENCE}"

        self.assertEqual(parse_full_rewrite(response, "python"), "b=2")

    def test_only_the_first_matching_block_is_returned(self):
        """``matches[0]`` at ``:110`` — later blocks are dropped silently."""
        response = f"{FENCE}python\nfirst=1\n{FENCE}\n{FENCE}python\nsecond=2\n{FENCE}"

        self.assertEqual(parse_full_rewrite(response, "python"), "first=1")

    def test_a_foreign_tag_falls_back_and_keeps_the_tag_as_the_first_line(self):
        """``:113-117`` — the any-fence group starts *before* the tag.

        Discriminating: a fallback that stripped the tag returns ``x = 1``.
        Asserting ``"x = 1" in result`` would pass under both behaviours, so
        the first line is asserted explicitly.
        """
        result = parse_full_rewrite(f"{FENCE}cpp\nx = 1\n{FENCE}", "python")

        self.assertEqual(result, "cpp\nx = 1")
        self.assertEqual(result.splitlines()[0], "cpp")

    def test_the_tag_match_is_byte_exact_and_newline_terminated(self):
        """``:106`` — ``language + "\\n"``. Near misses take the polluting path.

        A bare fence is the only clean fallback; ``python3``/``Python`` are one
        character away from the language and are not treated as it.
        """
        self.assertEqual(parse_full_rewrite(f"{FENCE}\nx = 1\n{FENCE}", "python"), "x = 1")
        self.assertEqual(
            parse_full_rewrite(f"{FENCE}python3\nx = 1\n{FENCE}", "python"), "python3\nx = 1"
        )
        self.assertEqual(
            parse_full_rewrite(f"{FENCE}Python\nx = 1\n{FENCE}", "python"), "Python\nx = 1"
        )
        # No newline after the tag: neither pattern sees a language.
        self.assertEqual(parse_full_rewrite(f"{FENCE}python x = 1{FENCE}", "python"), "python x = 1")

    def test_a_response_with_no_fence_is_returned_whole_and_never_none(self):
        """``:119-120`` — the ``Optional[str]`` annotation is unreachable.

        Discriminating: ``assertIsNotNone`` is *vacuous* here (no input returns
        ``None``), so the whole prose string is asserted by equality.
        """
        prose = "Sure! I'd restructure the loop, but I need the failing case first."

        self.assertEqual(parse_full_rewrite(prose, "python"), prose)

    def test_an_unclosed_fence_returns_the_response_with_its_markers_intact(self):
        """Both patterns need a closing fence; ``:120`` returns the raw text."""
        truncated = f"Here you go:\n{FENCE}python\nx = 1\n"

        self.assertEqual(parse_full_rewrite(truncated, "python"), truncated)

    def test_the_empty_string_is_the_only_falsy_return(self):
        """What the ``if not ...`` guards at the call sites can actually catch.

        Whitespace-only comes back *unstripped* and therefore truthy, so
        ``iteration_runner.py:882`` lets it through as child code.
        """
        self.assertEqual(parse_full_rewrite("", "python"), "")
        self.assertEqual(parse_full_rewrite("   \n  ", "python"), "   \n  ")
        self.assertTrue(parse_full_rewrite("   \n  ", "python"))

    def test_language_is_interpolated_into_the_regex_unescaped(self):
        """``:106`` concatenates ``language`` into the pattern without escaping.

        Discriminating: an escaped implementation returns the fallback string
        for every value below instead of raising. ``noema/config.py:164`` types
        ``language`` as a plain ``str``, so these values are reachable from
        config.
        """
        for language in ("(", "[a"):
            with self.subTest(language=language):
                with self.assertRaises(re.error):
                    parse_full_rewrite(f"{FENCE}x\ny\n{FENCE}", language)


class TestParseEvolveBlocks(unittest.TestCase):
    """``[up] openevolve/utils/code_utils.py:9-37``."""

    def test_blocks_are_zero_indexed_marker_lines_excluded_from_content(self):
        """``:29``/``:33`` — ``start_line`` is the START line, ``end_line`` the
        END line, and neither marker line is in the content."""
        code = "\n".join(
            [
                "head",  # 0
                "# EVOLVE-BLOCK-START",  # 1
                "a = 1",  # 2
                "# EVOLVE-BLOCK-END",  # 3
                "middle",  # 4
                "# EVOLVE-BLOCK-START",  # 5
                "b = 2",  # 6
                "# EVOLVE-BLOCK-END",  # 7
                "tail",  # 8
            ]
        )

        self.assertEqual(parse_evolve_blocks(code), [(1, 3, "a = 1"), (5, 7, "b = 2")])

    def test_a_second_start_silently_discards_everything_accumulated(self):
        """``:28-30`` resets ``block_content`` with no error and no log.

        Discriminating: ``len(blocks) == 1`` is *vacuous* — a parser that
        ignored the nested START also returns one block, but with content
        ``lost\\nkept``. The full tuple is asserted.
        """
        code = "\n".join(
            [
                "# EVOLVE-BLOCK-START",  # 0
                "lost = 1",  # 1
                "# EVOLVE-BLOCK-START",  # 2
                "kept = 2",  # 3
                "# EVOLVE-BLOCK-END",  # 4
            ]
        )

        self.assertEqual(parse_evolve_blocks(code), [(2, 4, "kept = 2")])

    def test_an_unterminated_block_is_dropped_entirely(self):
        """``:33`` only appends on END, so an open block is lost at return.

        Discriminating: a parser that closed the block at EOF returns
        ``[(1, 2, "body = 1")]``. The result is indistinguishable from code that
        declares no block at all — which is what ``boundary.py:58`` keys off.
        """
        self.assertEqual(parse_evolve_blocks("head\n# EVOLVE-BLOCK-START\nbody = 1"), [])

    def test_an_end_before_any_start_is_ignored(self):
        """``:31`` guards on ``in_block``, so the stray END is inert."""
        code = "\n".join(
            [
                "# EVOLVE-BLOCK-END",  # 0  stray
                "x = 0",  # 1
                "# EVOLVE-BLOCK-START",  # 2
                "y = 1",  # 3
                "# EVOLVE-BLOCK-END",  # 4
            ]
        )

        self.assertEqual(parse_evolve_blocks(code), [(2, 4, "y = 1")])

    def test_both_markers_on_one_line_open_a_block_that_never_closes(self):
        """``:27``'s ``if`` is checked before ``:31``'s ``elif``, so START wins
        and the END on the same line is never considered.

        Discriminating: the ``[]`` alone is vacuous — a parser that ignored the
        double-marker line entirely also returns ``[]``. The second case proves
        a block really was *opened* on line 0: a later END closes it, and the
        ignoring parser would return ``[]`` there too.
        """
        self.assertEqual(parse_evolve_blocks("# EVOLVE-BLOCK-START # EVOLVE-BLOCK-END\nx = 1"), [])
        self.assertEqual(
            parse_evolve_blocks("# EVOLVE-BLOCK-START # EVOLVE-BLOCK-END\nx = 1\n# EVOLVE-BLOCK-END"),
            [(0, 2, "x = 1")],
        )

    def test_markers_match_as_substrings_anywhere_in_the_line(self):
        """``:27``/``:31`` use ``in line``, not a prefix or exact match.

        Indentation, a trailing comment, and — the reachable case — a marker
        inside a string literal all open or close a block.
        """
        indented = "    # EVOLVE-BLOCK-START\nx = 1\n  # EVOLVE-BLOCK-END  # done"
        self.assertEqual(parse_evolve_blocks(indented), [(0, 2, "x = 1")])

        in_literal = 'MARKER = "# EVOLVE-BLOCK-START"\nx = 1\n# EVOLVE-BLOCK-END'
        self.assertEqual(parse_evolve_blocks(in_literal), [(0, 2, "x = 1")])

    def test_an_immediately_closed_block_yields_empty_content_not_absence(self):
        """``:33`` joins an empty list to ``""`` — the block still exists."""
        self.assertEqual(parse_evolve_blocks("# EVOLVE-BLOCK-START\n# EVOLVE-BLOCK-END"), [(0, 1, "")])
        self.assertEqual(parse_evolve_blocks(""), [])


PARENT = "\n".join(
    [
        "def main():",  # 0
        "    return strategy()",  # 1
        "# EVOLVE-BLOCK-START",  # 2
        "def strategy():",  # 3
        "    return 1",  # 4
        "# EVOLVE-BLOCK-END",  # 5
        "FOOTER = 1",  # 6
    ]
)


class TestNoemaCallSites(unittest.TestCase):
    """What the two parsers do to Noema's own seams (spec §2.2–§2.3)."""

    def test_a_marker_in_a_child_string_literal_guts_the_block_and_is_accepted(self):
        """Suspected bug 1, end to end through ``boundary.py:57-75``.

        The child writes the marker text into a string literal inside its own
        evolve block. ``parse_evolve_blocks`` re-opens at that line and discards
        everything before it, so the child arrives as one block holding only the
        lines *after* the literal — and passes the ``len(...) != 1`` rejection
        at ``:62``.

        Discriminating: ``assertIsNotNone`` is vacuous — being accepted is the
        bug. The restored interior is asserted equal to the truncated remnant (a
        correct parser keeps all three body lines), and the function signature
        the remnant needs is asserted gone.
        """
        child = "\n".join(
            [
                "def main():",
                "    return strategy()",
                "# EVOLVE-BLOCK-START",
                "def strategy():",
                '    kind = "# EVOLVE-BLOCK-START"',
                "    return 2",
                "# EVOLVE-BLOCK-END",
                "FOOTER = 1",
            ]
        )

        self.assertEqual(parse_evolve_blocks(child), [(4, 6, "    return 2")])

        restored = enforce_immutable_boundary(PARENT, child)

        self.assertIsInstance(restored, str)
        lines = restored.split("\n")
        start = lines.index("# EVOLVE-BLOCK-START")
        end = lines.index("# EVOLVE-BLOCK-END")
        self.assertEqual(lines[start + 1 : end], ["    return 2"])
        # The body survived; the ``def`` it belongs to did not — the accepted
        # child is an IndentationError, blamed on the model.
        self.assertNotIn("def strategy():", restored)

    def test_an_unterminated_parent_block_silently_disables_the_boundary(self):
        """Suspected bug 4. ``boundary.py:58-59`` cannot tell "malformed" from
        "opted out", so F_imm is never restored.

        Discriminating: the child is returned **byte-identical**, including the
        ``import os`` the parent never had. Enforcement would have restored the
        parent's ``def main`` head.
        """
        malformed_parent = "def main():\n    return strategy()\n# EVOLVE-BLOCK-START\nSEED = 1"
        self.assertEqual(parse_evolve_blocks(malformed_parent), [])

        child = "\n".join(
            [
                "import os",
                "def main():",
                "    return os.getpid()",
                "# EVOLVE-BLOCK-START",
                "def strategy():",
                "    return 2",
                "# EVOLVE-BLOCK-END",
            ]
        )

        self.assertEqual(enforce_immutable_boundary(malformed_parent, child), child)

    def test_materialize_hands_tag_polluted_code_to_the_population(self):
        """Suspected bug 2 at ``agenthost/materialize.py:37,40`` — both of the
        file's two ``parse_full_rewrite`` calls.

        ``:37`` is the diff-mode fallback (reached only when ``extract_diffs``
        finds nothing, so ``apply_diff`` is not the actor here) and ``:40`` is
        full-rewrite mode. Both return code whose first line is ``cpp``.
        """
        text = f"{FENCE}cpp\nx = 1\n{FENCE}"

        for parse_mode in ("full_rewrite", "diff"):
            with self.subTest(parse_mode=parse_mode):
                child = materialize_child_code(
                    text,
                    PARENT,
                    parse_mode=parse_mode,
                    language="python",
                    diff_pattern=r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE",
                )
                self.assertEqual(child, "cpp\nx = 1")

    def test_materialize_turns_a_prose_only_reply_into_child_code(self):
        """Suspected bug 3. ``materialize.py:38,41`` (``rewritten or text``)
        cannot filter a non-answer, because ``:120`` returns it verbatim.

        Discriminating: only ``""``/whitespace is rejected, and that rejection
        happens at ``materialize.py:29-30``, before the parser is reached.
        """
        prose = "I can't improve this without seeing the benchmark."

        self.assertEqual(
            materialize_child_code(
                prose, PARENT, parse_mode="full_rewrite", language="python", diff_pattern="x"
            ),
            prose,
        )
        self.assertIsNone(
            materialize_child_code(
                "   ", PARENT, parse_mode="full_rewrite", language="python", diff_pattern="x"
            )
        )

    def test_the_agenthost_fence_round_trip_truncates_code_holding_a_fence(self):
        """``agenthost/mutation_transport.py:70-71`` re-fences the materialized
        child to satisfy the ``-> str`` duck-type contract of
        ``MutationTransport.generate_with_context``.

        The fence regex is non-greedy (``:106``), so re-parsing that string cuts
        any program containing a triple backtick — a docstring with a markdown
        example — at the backtick. **Latent, not live**: ``:67`` also stashes the
        child on ``session._materialized_child``, and
        ``iteration_runner.py:339-343`` prefers that stash over
        ``_parse_response``, so the re-fenced string currently reaches only the
        attempt trace, not the population. Pinned because the hazard is one
        deleted short-circuit away, and because the trace records the truncation.

        Discriminating: the assertion is on the exact truncated string, and the
        round trip is asserted *not* to be the identity.
        """
        child_code = f'def strategy():\n    """usage: {FENCE}py\\nstrategy()\\n{FENCE}"""\n    return 1'
        re_fenced = f"{FENCE}python\n{child_code}\n{FENCE}"

        round_tripped = parse_full_rewrite(re_fenced, "python")

        self.assertNotEqual(round_tripped, child_code)
        self.assertEqual(round_tripped, 'def strategy():\n    """usage:')

    def test_call_site_composition_per_file(self):
        """Spec §2.2/§2.3: 4 calls / 3 files, and 2 calls / 1 file.

        Counts, not line numbers: a line pin would break on unrelated edits
        above the call. The facts worth pinning are that ``materialize.py``
        carries **two** ``parse_full_rewrite`` calls (the note's "3 sites"
        counts files) and that ``boundary.py`` is the sole consumer of
        ``parse_evolve_blocks``.
        """
        root = Path(__file__).resolve().parents[1] / "noema"
        counts: dict[str, Counter[str]] = {
            "parse_full_rewrite": Counter(),
            "parse_evolve_blocks": Counter(),
        }
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in counts:
                        counts[node.func.id][str(path.relative_to(root))] += 1

        self.assertEqual(
            dict(counts["parse_full_rewrite"]),
            {
                "agenthost/materialize.py": 2,
                "coordination/pe/module.py": 1,
                "evolution/iteration_runner.py": 1,
            },
        )
        self.assertEqual(sum(counts["parse_full_rewrite"].values()), 4)
        self.assertEqual(dict(counts["parse_evolve_blocks"]), {"evolution/boundary.py": 2})


if __name__ == "__main__":
    unittest.main()
