"""
Config-hardening tests (task 0056).

Item 1: a misspelled config key must fail loud, not be silently dropped and the
default used — which in a study where arms differ in exactly one setting could
quietly invalidate a comparison. Validation covers the top level and noema's own
sections; the borrowed openevolve sections stay lenient.
Item 3: the probation threshold is one shared constant (eviction == summary).
"""

import unittest

from noema.config import CoordinationConfig, NoemaConfig
from noema.coordination.hifo import insight_pool


class TestConfigTypoDetection(unittest.TestCase):
    def test_unknown_top_level_key_raises(self):
        with self.assertRaises(ValueError) as cm:
            NoemaConfig.from_dict({"diff_based_evoluton": True})  # typo
        self.assertIn("diff_based_evoluton", str(cm.exception))

    def test_unknown_key_in_a_noema_section_raises(self):
        # The arm-defining setting: a typo here would silently default to null.
        with self.assertRaises(ValueError) as cm:
            NoemaConfig.from_dict({"coordination": {"modul": "hifo"}})
        self.assertIn("modul", str(cm.exception))
        self.assertIn("coordination", str(cm.exception))

    def test_unknown_key_in_selection_section_raises(self):
        with self.assertRaises(ValueError):
            NoemaConfig.from_dict({"selection": {"polcy": "uct"}})

    def test_valid_config_still_parses(self):
        c = NoemaConfig.from_dict(
            {"random_seed": 7, "coordination": {"module": "hifo"}, "database": {"num_islands": 3}}
        )
        self.assertEqual(c.random_seed, 7)
        self.assertEqual(c.coordination.module, "hifo")
        self.assertEqual(c.database.num_islands, 3)

    def test_yaml_null_module_coerces_to_string_null(self):
        # PyYAML turns ``module: null`` into None; OFF arm is the string "null".
        c = NoemaConfig.from_dict({"coordination": {"module": None}})
        self.assertEqual(c.coordination.module, "null")

    def test_borrowed_openevolve_sections_stay_lenient(self):
        # openevolve's config key set is its contract, not noema's to police —
        # an unknown key there must NOT raise (matches openevolve's own from_dict).
        NoemaConfig.from_dict({"database": {"some_future_openevolve_key": 1}})

    def test_frozen_config_round_trips(self):
        # Regression guard: validation must never reject the config's OWN
        # serialized form (to_dict -> from_dict), or freezing/resume breaks.
        original = NoemaConfig(random_seed=11)
        restored = NoemaConfig.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())


class TestBanditMenuGuard(unittest.TestCase):
    """Task 0204: the bandit steers evolution solely by requesting an operator
    from the menu (coordination/bandit/module.py sampling_request). With the
    menu off, iteration_runner.choose_operator discards every request and
    falls back to the legacy path, but BanditModule.report_result still
    credits the requested arm — fabricated UCB statistics from a run that is
    byte-equivalent to null. Fail closed at construction instead."""

    def test_bandit_with_no_operator_menu_raises_at_construction(self):
        with self.assertRaises(ValueError) as cm:
            NoemaConfig(coordination=CoordinationConfig(module="bandit"))
        self.assertIn("bandit", str(cm.exception))
        self.assertIn("mutation_operators", str(cm.exception))

    def test_bandit_operators_disagreeing_with_menu_raises_at_construction(self):
        with self.assertRaises(ValueError) as cm:
            NoemaConfig(
                coordination=CoordinationConfig(
                    module="bandit", params={"operators": ["x1", "x2"]}
                ),
                mutation_operators=["e1", "e2", "m1", "m2", "m3"],
            )
        self.assertIn("x1", str(cm.exception))
        self.assertIn("x2", str(cm.exception))


class TestProbationConstant(unittest.TestCase):
    def test_eviction_and_summary_use_the_same_threshold(self):
        # Item 3: one source of truth for "probation".
        pool = insight_pool.InsightPool(initial_tips=["a tip that is long enough"])
        summary = pool.get_stats_summary()
        # A never-used tip is on probation by the same rule eviction uses.
        self.assertEqual(summary["probation_tips"], len(pool.tips))
        self.assertEqual(insight_pool.PROBATION_USAGE_COUNT, 3)


if __name__ == "__main__":
    unittest.main()
