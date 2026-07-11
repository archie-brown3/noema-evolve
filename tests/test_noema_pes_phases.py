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


class TestFaithfulBriefCheckpointRoundTrip(unittest.TestCase):
    """The faithful summarizer's storage split (task 0064) must survive a
    checkpoint: the full brief lives only in module state, so losing it on
    resume would silently downgrade later prompts to the capped slice."""

    def test_reflection_full_and_slice_round_trip(self):
        module = PESPlannerModule()
        module._plans["child-1"] = {
            "plan": "plan text",
            "outcome": IMPROVED,
            "parent_id": "parent-1",
            "parent_fitness": 0.5,
            "child_fitness": 0.7,
            "reflection_full": "**1. Executive Summary:**\nfull brief body",
            "reflection": "**1. Executive Summary:**\ncapped slice",
        }

        restored = PESPlannerModule()
        restored.load_state_dict(module.state_dict())

        entry = restored._plans["child-1"]
        self.assertEqual(
            entry["reflection_full"], "**1. Executive Summary:**\nfull brief body"
        )
        self.assertEqual(entry["reflection"], "**1. Executive Summary:**\ncapped slice")
        # log_snapshot counts the entry as reflected (it reads "reflection").
        self.assertEqual(restored.log_snapshot()["reflections_stored"], 1)

    def test_load_state_dict_accepts_entries_without_reflection_full(self):
        # pes-custom checkpoints (and any pre-0064 checkpoint) have no
        # reflection_full key; loading them must not fail.
        module = PESPlannerModule()
        module.load_state_dict(
            {
                "plans": {
                    "old-child": {
                        "plan": "p",
                        "outcome": "improved",
                        "parent_id": "old-parent",
                        "parent_fitness": 0.4,
                        "child_fitness": 0.6,
                        "reflection": "short custom note",
                    }
                },
                "pending_reflections": [],
            }
        )
        self.assertIsNone(module._plans["old-child"].get("reflection_full"))
        self.assertEqual(module._plans["old-child"]["reflection"], "short custom note")


if __name__ == "__main__":
    unittest.main()
