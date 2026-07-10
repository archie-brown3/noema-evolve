"""
Tests for the PES phase-class split (task 0060).

Covers the one behavioral addition of the split — `_plans` entries now carry
`parent_id` — and checkpoint tolerance for entries written before the key
existed. Everything else about the split is behavior-identical and pinned by
the pre-existing tests in test_noema_pes.py, which are unmodified.
"""

import unittest

from noema.coordination.base import GenerationContext
from noema.coordination.pes.module import IMPROVED, PESPlannerModule
from noema.substrate.views import ProgramView


def make_view(pid="p", fitness=0.5, code="def f():\n    return 1\n") -> ProgramView:
    return ProgramView(id=pid, code=code, fitness=fitness, metrics={"score": fitness})


def make_ctx(parent: ProgramView) -> GenerationContext:
    return GenerationContext(
        iteration=0,
        generation=0,
        island=0,
        parent=parent,
        best_fitness_history=[0.1, 0.2],
        avg_fitness_history=[0.05, 0.1],
    )


class TestPlansParentId(unittest.TestCase):
    def test_report_result_stores_parent_id_and_it_round_trips(self):
        module = PESPlannerModule()  # llm=None: no reflection enqueue needed here
        parent = make_view("parent-1", fitness=0.5)
        child = make_view("child-1", fitness=0.7)
        module.report_result(
            make_ctx(parent),
            child,
            {"plan": "# Plan\n\n## Strategy\n- x", "parent_id": "parent-1"},
            eval_failed=False,
        )

        entry = module._plans["child-1"]
        self.assertEqual(entry["parent_id"], "parent-1")
        self.assertEqual(entry["outcome"], IMPROVED)

        restored = PESPlannerModule()
        restored.load_state_dict(module.state_dict())
        self.assertEqual(restored._plans["child-1"]["parent_id"], "parent-1")

    def test_load_state_dict_accepts_entries_without_parent_id(self):
        # Checkpoints written before task 0060 have no parent_id in _plans
        # entries; loading them must not fail and must keep the entry usable.
        legacy_state = {
            "plans": {
                "old-child": {
                    "plan": "# Plan\n\n## Strategy\n- y",
                    "outcome": "improved",
                    "parent_fitness": 0.4,
                    "child_fitness": 0.6,
                }
            },
            "pending_reflections": [],
        }
        module = PESPlannerModule()
        module.load_state_dict(legacy_state)

        self.assertIn("old-child", module._plans)
        self.assertIsNone(module._plans["old-child"].get("parent_id"))
        self.assertEqual(module.log_snapshot()["plans_stored"], 1)


if __name__ == "__main__":
    unittest.main()
