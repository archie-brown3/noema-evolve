"""Config deep-merge + arm-parity tests (task 0112, spec §11, §16 items 2-3)."""

import unittest

from examples.kernelbench_coordination_smoke.config_loader import (
    ARM_NAMES,
    ArmOverlayError,
    assert_configs_differ_only_in_coordination_module,
    load_all_arm_configs,
    load_arm_config,
)
from noema.config import NoemaConfig


class TestArmConfigLoading(unittest.TestCase):
    def test_all_four_arms_load(self):
        configs = load_all_arm_configs()
        self.assertEqual(set(configs), set(ARM_NAMES))
        for cfg in configs.values():
            self.assertIsInstance(cfg, NoemaConfig)

    def test_unknown_arm_rejected(self):
        with self.assertRaises(ValueError):
            load_arm_config("not-a-real-arm")

    def test_module_is_set_correctly_per_arm(self):
        configs = load_all_arm_configs()
        self.assertEqual(configs["null"].coordination.module, "null")
        self.assertEqual(configs["hifo"].coordination.module, "hifo")
        self.assertEqual(configs["pes-faithful"].coordination.module, "pes-faithful")
        self.assertEqual(configs["bandit"].coordination.module, "bandit")

    def test_invariant_cell_values_from_base_yaml(self):
        # spec §4: 50,000-token cap, seed 42, islands substrate, 5-op menu.
        configs = load_all_arm_configs()
        for cfg in configs.values():
            self.assertEqual(cfg.budget.total_tokens, 50000)
            self.assertEqual(cfg.random_seed, 42)
            self.assertEqual(cfg.database.num_islands, 4)
            self.assertEqual(cfg.mutation_operators, ["e1", "e2", "m1", "m2", "m3"])
            self.assertTrue(cfg.retry_enabled)
            self.assertEqual(cfg.retry_on, "non_improvement")
            self.assertEqual(cfg.retry_cap, 2)
            self.assertFalse(cfg.prompt.use_template_stochasticity)


class TestArmParityInvariant(unittest.TestCase):
    """spec §16 item 2-3: arm overlays change ONLY coordination.module; all
    arms share operator menu, retry policy, seeds, token cap, prompt, substrate."""

    def test_all_four_built_configs_differ_only_in_coordination_module(self):
        configs = load_all_arm_configs()
        assert_configs_differ_only_in_coordination_module(configs)  # must not raise

    def test_yaml_rendering_diff_touches_only_module_lines(self):
        configs = load_all_arm_configs()
        names = list(configs)
        reference_lines = configs[names[0]].to_yaml().splitlines()
        for name in names[1:]:
            lines = configs[name].to_yaml().splitlines()
            self.assertEqual(len(lines), len(reference_lines))
            differing = [(a, b) for a, b in zip(reference_lines, lines) if a != b]
            self.assertTrue(differing, f"{name} config is byte-identical to {names[0]} — suspicious")
            for a, b in differing:
                self.assertIn("module", a)
                self.assertIn("module", b)

    def test_overlay_setting_anything_besides_module_is_rejected(self):
        from examples.kernelbench_coordination_smoke.config_loader import _validate_overlay_shape

        with self.assertRaises(ArmOverlayError):
            _validate_overlay_shape({"max_iterations": 5}, "fake")
        with self.assertRaises(ArmOverlayError):
            _validate_overlay_shape({"coordination": {"module": "hifo", "params": {}}}, "fake")
        with self.assertRaises(ArmOverlayError):
            _validate_overlay_shape({"coordination": {"seed": 1}}, "fake")
        _validate_overlay_shape({"coordination": {"module": "hifo"}}, "fake")  # must not raise

    def test_synthetic_drifted_overlay_is_caught_by_the_built_config_check(self):
        # Simulates a future base.yaml edit accidentally introducing drift
        # that only the POST-merge check (not the per-overlay shape check)
        # could catch.
        configs = load_all_arm_configs()
        drifted_dict = {**configs["hifo"].to_dict(), "max_iterations": 999}
        drifted = {**configs, "hifo": NoemaConfig.from_dict(drifted_dict)}
        with self.assertRaises(ArmOverlayError):
            assert_configs_differ_only_in_coordination_module(drifted)


if __name__ == "__main__":
    unittest.main()
