"""TDD characterization tests for the ReEvo short-term coordination arm."""

import asyncio
import random
import unittest

from noema.coordination import MODULE_REGISTRY, build_coordination_module
from noema.coordination.base import GenerationContext
from noema.coordination.reevo.module import REFLECTION_TAG, select_better_exemplar
from noema.coordination.reevo.prompts import (
    SYSTEM_REFLECTOR,
    donor_filter_code,
    reflection_code,
    render_short_term_reflection_prompt,
)
from noema.evolution.views import ProgramView
from noema.substrates.base import PopulationSnapshot


def program(identifier: str, fitness: float, code: str = "def f():\n    return 1") -> ProgramView:
    return ProgramView(id=identifier, code=code, fitness=fitness)


def context(parent=None, local=()):
    snapshot = PopulationSnapshot(
        scope="local",
        top_programs=tuple(local),
        fitnesses=tuple(item.fitness for item in local),
        topology="fixture",
    )
    return GenerationContext(
        iteration=3,
        generation=1,
        parent=parent,
        local_population=snapshot,
        global_population=snapshot,
    )


class FakeLLM:
    def __init__(self, response="  improve the boundary rule  "):
        self.response = response
        self.calls = []

    async def generate_with_context(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class TestReEvoShortTermModule(unittest.TestCase):
    def make_module(self, llm=None):
        return build_coordination_module(
            "reevo",
            {"domain_context": "maximize packing quality", "function_name": "heuristic"},
            llm=llm,
            rng=random.Random(0),
        )

    def test_registered(self):
        self.assertIn("reevo", MODULE_REGISTRY)

    def test_donor_filter_preserves_column_zero_semantics(self):
        code = "import x\nfrom y import z\ndef f():\n    value = 1\nreturn value\nignored = 2"
        self.assertEqual(donor_filter_code(code), "    value = 1\nreturn value")

    def test_reflection_code_uses_existing_evolve_block_extractor(self):
        code = "header\n# EVOLVE-BLOCK-START\ndef f():\n    return 7\n# EVOLVE-BLOCK-END\nfooter"
        self.assertEqual(reflection_code(code), "    return 7")

    def test_prompt_renders_worse_then_better(self):
        prompt = render_short_term_reflection_prompt(
            domain_context="a problem", function_name="h", worse_code="WORSE", better_code="BETTER"
        )
        self.assertLess(prompt.index("[Worse code]"), prompt.index("[Better code]"))
        self.assertLess(prompt.index("WORSE"), prompt.index("BETTER"))
        self.assertIn("less than 20 words", prompt)

    def test_selects_highest_strictly_fitter_distinct_program_with_id_tiebreak(self):
        parent = program("parent", 0.5)
        selected = select_better_exemplar(
            context(parent, [parent, program("z", 0.9), program("a", 0.9), program("equal", 0.5)])
        )
        self.assertEqual(selected.id, "z")

    def test_advice_calls_reflector_once_and_injects_exact_label(self):
        parent = program("parent", 0.5, "def f():\n    return 1")
        better = program("better", 0.8, "def f():\n    return 2")
        llm = FakeLLM()
        advice = asyncio.run(self.make_module(llm).advise(context(parent, [parent, better])))
        self.assertEqual(advice.prompt_block, "[Reflection]\nimprove the boundary rule")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["system_message"], SYSTEM_REFLECTOR)
        self.assertEqual(llm.calls[0]["tag"], REFLECTION_TAG)
        prompt = llm.calls[0]["messages"][0]["content"]
        self.assertIn("    return 1", prompt)
        self.assertIn("    return 2", prompt)
        self.assertEqual(advice.attribution["reevo"]["better_id"], "better")

    def test_no_parent_skips_without_calling_llm(self):
        llm = FakeLLM()
        advice = asyncio.run(self.make_module(llm).advise(context(None, [])))
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.attribution["reevo"]["reason"], "no_parent")
        self.assertEqual(llm.calls, [])

    def test_no_fitter_comparator_skips_without_calling_llm(self):
        parent = program("parent", 0.5)
        llm = FakeLLM()
        advice = asyncio.run(
            self.make_module(llm).advise(context(parent, [parent, program("worse", 0.4)]))
        )
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.attribution["reevo"]["reason"], "no_strictly_fitter_local_exemplar")
        self.assertEqual(llm.calls, [])

    def test_empty_response_is_not_injected(self):
        parent, better = program("parent", 0.5), program("better", 0.7)
        advice = asyncio.run(
            self.make_module(FakeLLM("   ")).advise(context(parent, [parent, better]))
        )
        self.assertEqual(advice.prompt_block, "")
        self.assertEqual(advice.attribution["reevo"]["status"], "empty_response")

    def test_state_is_memoryless(self):
        module = self.make_module(FakeLLM())
        self.assertEqual(module.state_dict(), {})
        module.load_state_dict({"ignored": True})
        self.assertEqual(module.state_dict(), {})


if __name__ == "__main__":
    unittest.main()
