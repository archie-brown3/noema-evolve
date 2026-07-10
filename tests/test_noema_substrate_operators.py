"""
Tests for noema.substrate.operators — the EoH-derived mutation operator menu
(Tree Substrate Plan / task 0027). Structural tests only here; prompt-
rendering and thought-extraction coverage lives in test_noema_prompts.py and
test_noema_controller.py once those pieces are wired (S3/S4).
"""

import unittest
from dataclasses import FrozenInstanceError

from noema.substrate.operators import OPERATOR_MENU, OPERATOR_TEMPLATES, OperatorSpec


class TestOperatorMenu(unittest.TestCase):
    def test_menu_has_exactly_five_operators(self):
        self.assertEqual(set(OPERATOR_MENU.keys()), {"e1", "e2", "m1", "m2", "m3"})

    def test_arity_matches_eoh_taxonomy(self):
        # e1/e2 are two-parent operators; m1/m2/m3 are single-parent.
        for name in ("e1", "e2"):
            self.assertEqual(OPERATOR_MENU[name].arity, 2, name)
        for name in ("m1", "m2", "m3"):
            self.assertEqual(OPERATOR_MENU[name].arity, 1, name)

    def test_parse_mode_matches_task_spec(self):
        for name in ("e1", "e2"):
            self.assertEqual(OPERATOR_MENU[name].parse_mode, "full_rewrite", name)
        for name in ("m1", "m2", "m3"):
            self.assertEqual(OPERATOR_MENU[name].parse_mode, "diff", name)

    def test_only_m3_has_no_thought(self):
        # EoH's own code-only exception (Table 5 ablation in the EoH paper
        # shows thought+code strongly outperforms code-only for every other
        # operator).
        for name in ("e1", "e2", "m1", "m2"):
            self.assertTrue(OPERATOR_MENU[name].has_thought, name)
        self.assertFalse(OPERATOR_MENU["m3"].has_thought)

    def test_i1_excluded(self):
        self.assertNotIn("i1", OPERATOR_MENU)

    def test_every_operator_has_a_registered_template(self):
        for spec in OPERATOR_MENU.values():
            self.assertIn(spec.template_key, OPERATOR_TEMPLATES)

    def test_operator_spec_is_frozen(self):
        spec = OPERATOR_MENU["m1"]
        with self.assertRaises(FrozenInstanceError):
            spec.name = "changed"


if __name__ == "__main__":
    unittest.main()
