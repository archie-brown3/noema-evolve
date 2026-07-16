"""
Full-controller-loop coverage for the PES arm (task 0040).

The 2026-07-09 review found PES was only ever tested in isolation (hand-called
module methods), which is exactly how two real bugs slipped past the suite and
were caught only by live cluster runs (the async-advise() interface bug and the
island-stamping bug). These drive the four remaining properties through a real
`NoemaController.run()`:

1. mutation prompts carry the PES plan as a suffix, and the shared prefix is
   byte-identical to a parallel Null-arm run;
2. children are distributed across islands (num_islands >= 2), not all island 0;
3. the reflection queue drains at the real generation-tick cadence, and a
   reflection reaches a later planning prompt for that lineage;
4. `child.metadata["stderr"]`, stamped by the real controller from the evaluator,
   reaches a reflection prompt.

pes-custom (advisory mode) is used so the plan is a suffix on the shared template;
the faithful/directive arm intentionally replaces the whole prompt (Decision #25),
so prefix identity does not apply to it.
"""

import asyncio
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import NoemaConfig
from noema.controller import NoemaController
from noema.coordination import NullCoordination, build_coordination_module

PLAN_TEXT = "# Plan\n\n## Strategy\n- return a larger constant"
REFLECTION_TEXT = "The constant was too small; return a bigger value next time."

EVAL_SCRIPT = (
    "import re\n"
    "def evaluate(program_path):\n"
    "    with open(program_path) as f:\n"
    "        code = f.read()\n"
    "    m = re.search(r'return (\\d+)', code)\n"
    "    value = float(m.group(1)) if m else 0.0\n"
    "    return {'combined_score': min(1.0, value / 10.0)}\n"
)

INITIAL = "def f():\n    return 1\n"


def _is_reflection_call(params):
    return any(
        "# Outcome to Explain" in m.get("content", "") for m in params.get("messages", [])
    )


def mutation_client():
    """Full-rewrite mutations with increasing constants (so children improve)."""
    calls = []
    counter = [0]

    async def create(**params):
        calls.append(params)
        counter[0] += 1
        content = f"```python\ndef f():\n    return {counter[0] + 1}\n```"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40),
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls
    )


def pes_coordination_client(plan_text=PLAN_TEXT, reflection_text=REFLECTION_TEXT):
    """Distinct responses for reflection vs planning calls."""
    calls = []

    async def create(**params):
        calls.append(params)
        content = reflection_text if _is_reflection_call(params) else plan_text
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=80),
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls
    )


def _config(iterations, num_islands=2, evaluator=None):
    return NoemaConfig(
        max_iterations=iterations,
        checkpoint_interval=100,
        diff_based_evolution=False,  # full rewrites
        database=DatabaseConfig(
            in_memory=True, num_islands=num_islands, population_size=50,
            random_seed=42, migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
    )


def _build(tmp, arm, iterations, coordination_client=None, evaluator=None, **params):
    eval_path = os.path.join(tmp, "evaluator.py")
    if not os.path.exists(eval_path):
        with open(eval_path, "w") as f:
            f.write(EVAL_SCRIPT)
    ledger = TokenLedger(total_budget_tokens=1_000_000)
    mut = mutation_client()
    mutation_llm = BudgetedLLM(model="fake", ledger=ledger, account="mutation",
                              tag="mutate", client=mut, retries=0, retry_delay=0.0)
    if arm == "null":
        coordination = NullCoordination()
    else:
        cc = coordination_client or pes_coordination_client()
        coordination_llm = BudgetedLLM(model="fake", ledger=ledger, account="coordination",
                                       tag="pes.coordination", client=cc, retries=0, retry_delay=0.0)
        coordination = build_coordination_module(
            arm, params, llm=coordination_llm, rng=random.Random(0)
        )
        coordination._cc = cc  # expose for assertions
    controller = NoemaController(
        config=_config(iterations, evaluator=evaluator),
        evaluation_file=eval_path,
        initial_program_code=INITIAL,
        output_dir=os.path.join(tmp, f"out_{arm}"),
        mutation_llm=mutation_llm,
        coordination=coordination,
        ledger=ledger,
    )
    if evaluator is not None:
        controller.evaluator = evaluator
    return controller, ledger, mut


class TestPESPromptIdentity(unittest.TestCase):
    def test_pes_plan_is_a_suffix_on_the_null_prefix(self):
        with tempfile.TemporaryDirectory() as t_off, tempfile.TemporaryDirectory() as t_pes:
            null_c, null_led, null_mut = _build(t_off, "null", 4)
            pes_c, pes_led, pes_mut = _build(t_pes, "pes-custom", 4)
            asyncio.run(null_c.run())
            asyncio.run(pes_c.run())

            self.assertEqual(len(null_mut.calls), len(pes_mut.calls))
            found_plan = False
            for off_call, on_call in zip(null_mut.calls, pes_mut.calls):
                off_user = off_call["messages"][-1]["content"]
                on_user = on_call["messages"][-1]["content"]
                # Shared prefix byte-identical; PES only appends a suffix.
                self.assertTrue(on_user.startswith(off_user))
                if len(on_user) > len(off_user):
                    self.assertIn("Strategy", on_user[len(off_user):])
                    found_plan = True
            self.assertTrue(found_plan, "PES never appended a plan through the loop")
            # Ledger splits by account; mutation spend identical across arms.
            self.assertEqual(null_led.spent("coordination"), 0)
            self.assertGreater(pes_led.spent("coordination"), 0)
            self.assertEqual(null_led.spent("mutation"), pes_led.spent("mutation"))


class TestPESIslandDistribution(unittest.TestCase):
    def test_children_land_across_islands_not_all_island_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = _build(tmp, "pes-custom", 6, num_islands=2)
            asyncio.run(controller.run())
            children = [
                p for p in controller.db._db.programs.values() if p.parent_id is not None
            ]
            self.assertTrue(children)
            self.assertEqual({c.metadata["island"] for c in children}, {0, 1})
            self.assertTrue(controller.db.island_fitnesses(0))
            self.assertTrue(controller.db.island_fitnesses(1))


class TestPESReflectionThroughController(unittest.TestCase):
    def test_reflection_drains_at_tick_and_reaches_a_later_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            # num_islands=1 so the same lineage is re-selected and its reflection
            # can reach a later planning prompt within a short run.
            controller, _, _ = _build(tmp, "pes-custom", 8, num_islands=1)
            asyncio.run(controller.run())
            module = controller.coordination

            # Drained at the real generation-tick cadence.
            self.assertEqual(len(module._pending_reflections), 0)
            # A reflection call actually happened through the loop...
            calls = module._cc.calls
            self.assertTrue(any(_is_reflection_call(c) for c in calls))
            # ...and a later planning prompt carried the reflection for that lineage.
            planning_with_reflection = [
                c for c in calls
                if not _is_reflection_call(c)
                and REFLECTION_TEXT in c["messages"][-1]["content"]
            ]
            self.assertTrue(
                planning_with_reflection,
                "reflection never reached a subsequent planning prompt",
            )

    def test_controller_stamped_stderr_reaches_reflection_prompt(self):
        # A recorded child whose evaluation emitted stderr: the REAL controller
        # stamps child.metadata["stderr"] from the evaluator artifacts, and PES's
        # reflection prompt must carry it. (The controller pairs eval_failed with
        # child=None, so the reachable path is a recorded child that emitted
        # stderr — not a failed one; see the module docstring.)
        class StderrEvaluator:
            async def evaluate_program(self, code, program_id):
                import re
                m = re.search(r"return (\d+)", code)
                return {"combined_score": min(1.0, (float(m.group(1)) if m else 0.0) / 10.0)}

            def get_pending_artifacts(self, program_id):
                return {"stderr": "WARN-MARKER-42: noisy but valid"}

        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = _build(
                tmp, "pes-custom", 6, num_islands=1, evaluator=StderrEvaluator()
            )
            asyncio.run(controller.run())
            calls = controller.coordination._cc.calls
            reflection_calls = [c for c in calls if _is_reflection_call(c)]
            self.assertTrue(reflection_calls, "no reflection happened through the loop")
            self.assertTrue(
                any("WARN-MARKER-42" in c["messages"][-1]["content"] for c in reflection_calls),
                "controller-stamped stderr never reached a reflection prompt",
            )


if __name__ == "__main__":
    unittest.main()
