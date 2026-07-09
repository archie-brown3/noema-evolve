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
from noema.controller import NoemaController


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


class TestRetryPromptSuffix(unittest.TestCase):
    def test_retry_suffix_structure(self):
        suffix = NoemaController._build_retry_suffix(
            None, error_text="IndexError: list index out of range", attempt=1
        )
        self.assertIn("# Retry After Failure", suffix)
        self.assertIn("Your previous attempt failed", suffix)
        self.assertIn("IndexError: list index out of range", suffix)
        self.assertIn("Produce a corrected program", suffix)
        self.assertIn("Re-output the full code", suffix)

    def test_retry_suffix_includes_error_text(self):
        suffix = NoemaController._build_retry_suffix(
            None, error_text="no parseable code block found in the response", attempt=2
        )
        self.assertIn("no parseable code block found in the response", suffix)

    def test_retry_suffix_is_arm_agnostic(self):
        # Same method, same output regardless of coordination module
        suffix = NoemaController._build_retry_suffix(
            None, error_text="generated code length 15000 exceeds max 10000", attempt=0
        )
        self.assertIn("generated code length 15000 exceeds max 10000", suffix)
        self.assertNotIn("reflection", suffix.lower())
        self.assertNotIn("plan", suffix.lower())

    def test_reflection_suffix_structure(self):
        # The PES reflection block appended after the raw-error retry suffix.
        # Locked substrings per spec (prompt-identity guard for Stage 2).
        reflection_text = "The loop overran the array bound; cap the index at n-1."
        reflection_suffix = (
            "\n# Reflection on the lineage's last failure\n"
            f"{reflection_text}\n"
            "Use this causal explanation to guide the corrected mutation."
        )
        self.assertIn("# Reflection on the lineage's last failure", reflection_suffix)
        self.assertIn("Use this causal explanation", reflection_suffix)
        self.assertIn(reflection_text, reflection_suffix)


if __name__ == "__main__":
    unittest.main()
