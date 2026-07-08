"""
Tests for noema.substrate.prompts — the identical-prompts-across-arms guarantee
"""

import unittest
import uuid

from openevolve.config import PromptConfig
from openevolve.database import Program

from noema.substrate.prompts import (
    COORDINATION_HEADER,
    build_mutation_prompt,
    inject_advice,
    make_prompt_sampler,
)


def make_parent() -> Program:
    return Program(
        id=str(uuid.uuid4()),
        code="def f():\n    return 1\n",
        language="python",
        metrics={"combined_score": 0.5},
    )


def build(sampler, parent):
    return build_mutation_prompt(
        sampler,
        parent=parent,
        top_programs=[],
        previous_programs=[],
        inspirations=[],
        language="python",
        iteration=3,
        diff_based_evolution=True,
        feature_dimensions=["complexity", "diversity"],
    )


class TestPromptAssembly(unittest.TestCase):
    def test_stochasticity_rejected(self):
        with self.assertRaises(ValueError):
            make_prompt_sampler(PromptConfig(use_template_stochasticity=True))

    def test_prompt_deterministic_across_builds(self):
        parent = make_parent()
        sampler_a = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        sampler_b = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt_a = build(sampler_a, parent)
        prompt_b = build(sampler_b, parent)
        self.assertEqual(prompt_a["system"], prompt_b["system"])
        self.assertEqual(prompt_a["user"], prompt_b["user"])

    def test_empty_advice_is_byte_identical(self):
        # The coordination-OFF arm: injecting empty blocks must not change a byte
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt = build(sampler, make_parent())
        injected = inject_advice(prompt, prompt_block="", system_block="")
        self.assertEqual(injected["system"], prompt["system"])
        self.assertEqual(injected["user"], prompt["user"])

    def test_advice_appends_suffix_only(self):
        # The coordination-ON arm: shared prefix stays byte-identical, block is a suffix
        sampler = make_prompt_sampler(PromptConfig(use_template_stochasticity=False))
        prompt = build(sampler, make_parent())
        injected = inject_advice(
            prompt, prompt_block="- Use vectorized operations", system_block="Focus on speed."
        )
        self.assertTrue(injected["user"].startswith(prompt["user"]))
        self.assertTrue(injected["system"].startswith(prompt["system"]))
        self.assertEqual(
            injected["user"],
            prompt["user"] + COORDINATION_HEADER + "- Use vectorized operations",
        )
        self.assertIn("Focus on speed.", injected["system"])


if __name__ == "__main__":
    unittest.main()
