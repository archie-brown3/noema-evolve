"""0188 Stage 2: characterization of upstream ``get_fitness_score``.

Noema-authored characterization tests (not pin-regression): they describe what
the *installed upstream* function does today, so that any change to it — an
upstream bump, a local patch — shows up as a failure here rather than as a
silently different fitness scalar in every arm of the ablation.

Grounding: vault note "0188 OpenEvolve Fidelity Spec §6 — Scope Table —
2026-08-05" §4, against ``openevolve/utils/metrics_utils.py`` at pin ``80945ed``
(``get_fitness_score`` at ``:69-114``, ``safe_numeric_average`` at ``:8-37``).
Upstream test coverage of this function is **zero** — ``grep -rn
"get_fitness_score\\|metrics_utils" tests/upstream/`` matches nothing across all
44 donor test files — which is why these are written rather than vendored.

## PIN, DON'T FIX

Several behaviours pinned below are suspected bugs. They are pinned exactly as
they behave, with no edit under ``noema/`` and none to upstream. Flagged for the
orchestrator in the stage log:

1. B4 "backward compatibility" trap (``:110-112``) averages **all** metrics
   including the feature dimensions B3 just spent excluding — the precise
   pollution the docstring at ``:73-76`` says the function exists to prevent.
   Reachable in normal MAP-Elites operation, not only pathologically.
2. B2 (``:88-91``) returns ``combined_score`` with **no NaN check**, unlike
   every other path, so NaN becomes fitness.
3. ``OverflowError`` asymmetry: B2's ``except`` (``:92``) omits it while both
   numeric loops (``:62``, ``:107``) catch it, so a huge int under
   ``combined_score`` raises out of the function and the same value under any
   other key is silently skipped.
4. ``bool`` passes ``isinstance(value, (int, float))`` (``:102``, ``:56``), so
   boolean metrics silently enter the fitness average as 1.0/0.0.
5. B2′ (``:92-93``) falls through with no log and no warning.

Nine of the ten Noema call sites consume the return value unguarded; only
``noema/selection/uct.py:257-263`` rejects a non-finite result. That asymmetry
is pinned in ``TestCallSiteShapes``.
"""

from __future__ import annotations

import ast
import logging
import math
import unittest
from collections import Counter
from pathlib import Path

from openevolve.config import DatabaseConfig
from openevolve.database import Program
from openevolve.utils.metrics_utils import get_fitness_score

from noema.evolution.views import ProgramView
from noema.selection.uct import UCTSelectionPolicy
from noema.substrates.database import SubstrateDatabase
from noema.substrates.flat import FlatPopulationStore
from noema.substrates.tree import TreeStore


NAN = float("nan")
HUGE_INT = 10**400  # float() raises OverflowError, not ValueError/TypeError


def program(program_id: str, metrics: dict, parent_id=None) -> Program:
    return Program(
        id=program_id,
        code=f"# {program_id}\n",
        language="python",
        parent_id=parent_id,
        metrics=dict(metrics),
    )


class TestFourBranches(unittest.TestCase):
    """The four documented branches, upstream ``:85-114``."""

    def test_b1_empty_metrics_returns_zero(self):
        """``:85-86`` — falsy metrics short-circuit before anything else."""

        self.assertEqual(get_fitness_score({}), 0.0)
        self.assertEqual(get_fitness_score({}, ["f1"]), 0.0)

    def test_b2_combined_score_wins_and_is_coerced_through_float(self):
        """``:88-91`` — every other metric is ignored, including feature dims.

        The string case pins that the coercion is ``float(...)`` and not an
        ``isinstance`` gate: ``"0.75"`` is not numeric but is returned as 0.75.
        """

        self.assertEqual(get_fitness_score({"combined_score": 0.75, "other": 0.0}), 0.75)
        self.assertEqual(get_fitness_score({"combined_score": 3}), 3.0)
        self.assertEqual(get_fitness_score({"combined_score": "0.75", "x": 1.0}), 0.75)
        # feature_dimensions is not even read on this path
        self.assertEqual(
            get_fitness_score({"combined_score": 0.75, "f1": 9.0}, ["f1", "combined_score"]),
            0.75,
        )

    def test_b2_prime_unconvertible_combined_score_falls_through_silently(self):
        """``:92-93`` — ``except (ValueError, TypeError): pass``, no log.

        The program is then scored on its *other* metrics as if
        ``combined_score`` had never been present: it is a ``str``, so it also
        fails B3's ``isinstance`` test at ``:102`` and never reaches the average.
        A naive reading would expect ``(1.0 + 2.0) / 3`` counting the excluded
        key; the pinned value is the two-metric mean.
        """

        with self.assertLogs(level=logging.DEBUG) as captured:
            # assertLogs requires at least one record, so emit a sentinel and
            # assert nothing else joined it.
            logging.getLogger("noema.test.sentinel").debug("sentinel")
            result = get_fitness_score({"combined_score": "abc", "x": 1.0, "y": 2.0})
        self.assertEqual(result, 1.5)
        self.assertEqual([record.getMessage() for record in captured.records], ["sentinel"])

        # None hits TypeError rather than ValueError; same silent fall-through.
        self.assertEqual(get_fitness_score({"combined_score": None, "x": 4.0}), 4.0)

    def test_b3_averages_non_feature_numeric_metrics_with_the_survivor_divisor(self):
        """``:95-108`` + ``:114`` — the divisor is the survivor count.

        ``{"a": 1.0, "b": "x", "c": nan, "d": 3.0}`` averages to 2.0 (two
        survivors), not 1.0 (four metrics). Non-numeric and NaN entries are
        dropped from the numerator *and* the denominator.
        """

        self.assertEqual(get_fitness_score({"a": 1.0, "b": "x", "c": NAN, "d": 3.0}), 2.0)
        # feature dimensions are excluded before the average is taken
        self.assertEqual(get_fitness_score({"score": 1.0, "f1": 9.0}, ["f1"]), 1.0)
        self.assertEqual(
            get_fitness_score({"score": 1.0, "other": 3.0, "f1": 100.0}, ["f1"]), 2.0
        )

    def test_b4_trap_falls_back_to_all_metrics_including_feature_dimensions(self):
        """``:110-112`` — SUSPECTED BUG, pinned as-is.

        When no non-feature metric survives, the function averages the *whole*
        dict, feature dimensions included. A MAP-Elites program whose metrics
        are exactly its feature coordinates is scored on those coordinates —
        the pollution the docstring at ``:73-76`` says B3 exists to prevent.
        The honest value would be 0.0 (no fitness signal); the pinned value is
        the mean of the coordinates.
        """

        self.assertEqual(get_fitness_score({"f1": 2.0, "f2": 4.0}, ["f1", "f2"]), 3.0)
        # non-numeric keys still do not reach the divisor on the fallback path
        self.assertEqual(
            get_fitness_score({"f1": 2.0, "f2": 4.0, "note": "hi"}, ["f1", "f2"]), 3.0
        )
        # a non-feature key that fails the numeric test does not save B3
        self.assertEqual(get_fitness_score({"f1": 6.0, "label": "x"}, ["f1"]), 6.0)
        # ``safe_numeric_average`` ``:34-35`` — no survivors is 0.0, not a raise
        self.assertEqual(get_fitness_score({"label": "x"}), 0.0)


class TestSharpEdges(unittest.TestCase):
    """The three sharp edges beyond the four branches (spec §6 §4)."""

    def test_overflow_error_escapes_uncaught_only_from_combined_score(self):
        """SUSPECTED BUG, pinned as-is: ``:92`` omits ``OverflowError``.

        Both halves matter — the asymmetry *is* the behaviour. Under
        ``combined_score`` the exception escapes the function entirely; under
        any other key ``:107`` catches it and the value is silently skipped.
        """

        with self.assertRaises(OverflowError):
            get_fitness_score({"combined_score": HUGE_INT})

        self.assertEqual(get_fitness_score({"x": HUGE_INT, "y": 1.0}), 1.0)
        # ...and the skip removes it from the divisor too, not just the sum
        self.assertEqual(get_fitness_score({"x": HUGE_INT, "y": 1.0, "z": 3.0}), 2.0)
        # the B4 fallback path skips it identically (``:29``)
        self.assertEqual(get_fitness_score({"f1": HUGE_INT, "f2": 5.0}, ["f1", "f2"]), 5.0)

    def test_bool_is_admitted_as_numeric(self):
        """SUSPECTED BUG, pinned as-is: ``isinstance(True, int)`` is True.

        ``{"a": 2.0, "flag": True}`` is 1.5 because the flag entered as 1.0;
        had bools been skipped it would be 2.0.
        """

        self.assertEqual(get_fitness_score({"a": 2.0, "flag": True}), 1.5)
        self.assertEqual(get_fitness_score({"a": 2.0, "flag": False}), 1.0)
        # and a bool alone is a fitness value, not an absent metric
        self.assertEqual(get_fitness_score({"flag": True}), 1.0)
        self.assertEqual(get_fitness_score({"combined_score": True}), 1.0)

    def test_nan_escapes_through_the_combined_score_branch(self):
        """SUSPECTED BUG, pinned as-is: B2 (``:88-91``) has no NaN check.

        Every other path filters NaN (``:105``, ``:28``). This one returns it,
        so NaN becomes the fitness scalar.
        """

        self.assertTrue(math.isnan(get_fitness_score({"combined_score": NAN})))
        self.assertTrue(math.isnan(get_fitness_score({"combined_score": "nan", "x": 1.0})))
        # contrast: the same NaN under any other key is filtered out
        self.assertEqual(get_fitness_score({"score": NAN, "x": 1.0}), 1.0)
        # infinities are not NaN, so they pass every path unfiltered
        self.assertEqual(get_fitness_score({"combined_score": float("inf")}), float("inf"))
        self.assertEqual(get_fitness_score({"score": float("inf"), "x": 1.0}), float("inf"))


class TestCallSiteShapes(unittest.TestCase):
    """The metric-dict shapes Noema's ten call sites can pass (spec §6 §2.1)."""

    def test_call_site_composition_per_file(self):
        """Ten calls across seven files; ``islands.py`` carries none.

        Counts, not line numbers: a line-number pin would break on unrelated
        edits above the call. The two facts worth pinning are that ``tree.py``
        carries three sites and that ``IslandsStore`` reaches fitness only
        indirectly, through ``SubstrateDatabase``.
        """

        root = Path(__file__).resolve().parents[1] / "noema"
        counts: Counter[str] = Counter()
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text())
            calls = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "get_fitness_score"
            )
            if calls:
                counts[str(path.relative_to(root))] = calls

        self.assertEqual(
            dict(counts),
            {
                "evolution/iteration_runner.py": 2,
                "evolution/views.py": 1,
                "selection/uct.py": 1,
                "substrates/cvt.py": 1,
                "substrates/database.py": 1,
                "substrates/flat.py": 1,
                "substrates/tree.py": 3,
            },
        )
        self.assertEqual(sum(counts.values()), 10)
        self.assertNotIn("substrates/islands.py", counts)

    def test_stores_and_views_pass_their_feature_dimensions_through(self):
        """The shared shape of eight sites: ``(metrics, feature_dimensions)``.

        ``flat.py:87``, ``tree.py:114``, ``views.py:34``, ``database.py:72`` are
        exercised directly; ``cvt.py:201`` and ``iteration_runner.py:497,713``
        pass the identical two-argument form (a store's ``feature_dimensions``
        beside a program's ``metrics``) and are covered by the composition test
        above rather than by standing up KMeans and a full host.
        """

        metrics = {"score": 1.0, "f1": 9.0}
        prog = program("p1", metrics)

        flat = FlatPopulationStore(population_size=4, feature_dimensions=("f1",))
        tree = TreeStore(feature_dimensions=("f1",))
        database = SubstrateDatabase(DatabaseConfig(feature_dimensions=["f1"], in_memory=True))

        self.assertEqual(flat.fitness(prog), 1.0)
        self.assertEqual(tree.fitness(prog), 1.0)
        self.assertEqual(database.fitness(prog), 1.0)
        self.assertEqual(ProgramView.from_program(prog, ["f1"]).fitness, 1.0)

        # ...and every one of them takes the B4 trap on a coordinates-only
        # program, scoring it on the feature dimensions themselves.
        trapped = program("p2", {"f1": 9.0})
        self.assertEqual(flat.fitness(trapped), 9.0)
        self.assertEqual(tree.fitness(trapped), 9.0)
        self.assertEqual(database.fitness(trapped), 9.0)
        self.assertEqual(ProgramView.from_program(trapped, ["f1"]).fitness, 9.0)

    def test_tree_working_set_ranking_consumes_the_same_scalar(self):
        """``tree.py:135,144`` — the two ranking calls, same shape as ``:114``.

        Ranking is by fitness descending, so the B4-trapped program outranks
        the honestly-scored one on the strength of its feature coordinate.
        """

        programs = {
            "low": program("low", {"score": 1.0, "f1": 9.0}),
            "trap": program("trap", {"f1": 9.0}),
        }
        self.assertEqual(
            TreeStore._working_ids_for(programs, size=2, feature_dimensions=("f1",)),
            ["trap", "low"],
        )

    def test_uct_is_the_only_call_site_that_rejects_a_non_finite_score(self):
        """``uct.py:257-263`` guards; the other nine sites propagate.

        A NaN escaping B2 raises inside UCT and becomes fitness everywhere else.
        """

        store = TreeStore()
        store.add(program("seed", {"combined_score": 0.0}))
        selector = UCTSelectionPolicy(
            token_budget=1_000,
            initial_exploration=0.1,
            widening_alpha=0.5,
            random_seed=7,
        )
        selection = selector.select(store, target_scope=None, num_inspirations=0)
        child = program("child", {"combined_score": NAN}, selection.parent.id)

        with self.assertRaises(ValueError):
            selector.on_child_accepted(parent=selection.parent, child=child, step_size=0.5)

        # the unguarded sites take the same NaN without complaint
        self.assertTrue(math.isnan(store.fitness(child)))
        self.assertTrue(math.isnan(ProgramView.from_program(child).fitness))
        self.assertTrue(
            math.isnan(FlatPopulationStore(population_size=4).fitness(child))
        )


if __name__ == "__main__":
    unittest.main()
