"""Pure section-walk state tests for configure CLI (task 0189 / Phase 3)."""

from __future__ import annotations

import unittest

from noema.agenthost.configure_walk import SECTION_ORDER, ConfigureWalk


class TestConfigureWalkOrder(unittest.TestCase):
    def test_section_order_is_primary_then_advanced_then_write(self):
        self.assertEqual(
            SECTION_ORDER,
            (
                "paths",
                "agent",
                "coordination",
                "substrate",
                "selection",
                "evolution",
                "overview",
                "write_and_run",
            ),
        )

    def test_new_walk_starts_on_paths(self):
        walk = ConfigureWalk()
        self.assertEqual(walk.section_id, "paths")
        self.assertEqual(walk.section_index, 0)

    def test_idle_left_right_changes_section(self):
        walk = ConfigureWalk()
        walk.move_section(+1)
        self.assertEqual(walk.section_id, "agent")
        walk.move_section(+1)
        self.assertEqual(walk.section_id, "coordination")
        walk.move_section(-1)
        self.assertEqual(walk.section_id, "agent")

    def test_section_move_clamps_at_ends(self):
        walk = ConfigureWalk()
        walk.move_section(-1)
        self.assertEqual(walk.section_id, "paths")
        walk.section_index = len(SECTION_ORDER) - 1
        walk.move_section(+1)
        self.assertEqual(walk.section_id, "write_and_run")

    def test_enter_arms_esc_discards_enter_accepts(self):
        walk = ConfigureWalk(
            sections={
                "paths": [{"id": "x", "kind": "open", "value": "1"}],
                "agent": [],
                "coordination": [],
                "advanced": [],
                "write_and_run": [],
            }
        )
        walk.arm()
        self.assertTrue(walk.armed)
        walk.current_field()["value"] = "2"
        walk.disarm(discard=True)
        self.assertFalse(walk.armed)
        self.assertFalse(walk.dirty)
        self.assertEqual(walk.current_field()["value"], "1")
        walk.arm()
        walk.current_field()["value"] = "3"
        walk.disarm(discard=False)
        self.assertFalse(walk.armed)
        self.assertTrue(walk.dirty)
        self.assertEqual(walk.current_field()["value"], "3")

    def test_armed_blocks_section_move(self):
        walk = ConfigureWalk(
            sections={
                "paths": [{"id": "x", "kind": "open", "value": "1"}],
                "agent": [],
                "coordination": [],
                "advanced": [],
                "write_and_run": [],
            }
        )
        walk.arm()
        walk.move_section(+1)
        self.assertEqual(walk.section_id, "paths")

    def test_armed_left_right_cycles_closed_field(self):
        walk = ConfigureWalk(
            sections={
                "paths": [
                    {
                        "id": "config",
                        "kind": "closed",
                        "choices": ["a.yaml", "b.yaml", "(new) config.yaml"],
                        "value": "a.yaml",
                    },
                ]
            }
        )
        walk.arm()
        walk.cycle_value(+1)
        self.assertEqual(walk.current_field()["value"], "b.yaml")
        walk.cycle_value(+1)
        self.assertEqual(walk.current_field()["value"], "(new) config.yaml")
        walk.cycle_value(+1)
        self.assertEqual(walk.current_field()["value"], "a.yaml")
        walk.cycle_value(-1)
        self.assertEqual(walk.current_field()["value"], "(new) config.yaml")


if __name__ == "__main__":
    unittest.main()
