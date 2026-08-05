"""Controller-backed upstream semantics specs for task 0188.

We do not copy upstream tests verbatim here.  Upstream projects construct their
own databases, prompt builders, or process controllers directly; Noema's claim
is different: a configured ``NoemaController`` should compose the matching
substrate, selection policy, and prompt sampler.  These tests therefore use a
small Noema-owned controller harness plus thin adapters that encode the borrowed
upstream semantics.
"""

from __future__ import annotations

import asyncio
import random
import tempfile
import unittest
from dataclasses import dataclass, field
from typing import Any, List

from openevolve.config import DatabaseConfig
from openevolve.database import Program

from noema.config import NoemaConfig, SelectionConfig, SubstrateConfig
from noema.selection.stock_openevolve import StockOpenEvolveSelection
from noema.substrates.islands import IslandsStore
from tests.test_noema_controller import make_config, make_controller


def _islands_stock_config(*, num_islands: int = 3, max_iterations: int = 4) -> NoemaConfig:
    return make_config(
        max_iterations=max_iterations,
        database=DatabaseConfig(
            in_memory=True,
            num_islands=num_islands,
            archive_size=20,
            population_size=100,
            random_seed=42,
            migration_interval=1000,
        ),
        substrate=SubstrateConfig(kind="islands"),
        selection=SelectionConfig(policy="stock_openevolve"),
    )


def _program(program_id: str, score: float, *, island: int) -> Program:
    return Program(
        id=program_id,
        code=f"def {program_id}():\n    return {score}\n",
        language="python",
        metrics={"combined_score": score},
        metadata={"island": island},
    )


@dataclass
class NoemaControllerHarness:
    """Noema-owned harness used by upstream semantic adapters.

    It deliberately exposes behavior at the controller boundary: configured
    runtime objects, parent selection, prompt building, and iteration sampling.
    """

    tmp: str
    config: NoemaConfig
    controller: Any = field(init=False)
    ledger: Any = field(init=False)
    client: Any = field(init=False)

    def __post_init__(self) -> None:
        self.controller, self.ledger, self.client = make_controller(
            self.tmp,
            config=self.config,
        )

    def populate_islands(self, *, per_island: int = 6) -> None:
        for island in range(self.controller.db.num_islands):
            for offset in range(per_island):
                self.controller.db.add(
                    _program(f"i{island}_p{offset}", island + offset / 10, island=island),
                    target_scope=island,
                )

    def select(self, *, target_scope: int, num_inspirations: int = 0):
        return self.controller.substrate.select(
            target_scope=target_scope,
            num_inspirations=num_inspirations,
        )

    def record_iteration_sample_scopes(self) -> List[int]:
        sampled_scopes: List[int] = []
        original = self.controller.db._db.sample_from_island

        def recording_sample_from_island(island_id, num_inspirations=None):
            sampled_scopes.append(island_id)
            return original(island_id, num_inspirations=num_inspirations)

        self.controller.db._db.sample_from_island = recording_sample_from_island
        asyncio.run(self.controller.run())
        return sampled_scopes

    def build_prompt(self):
        return self.controller.sampler.build_prompt(
            current_program="def test():\n    pass\n",
            parent_program="def test():\n    pass\n",
            program_metrics={"combined_score": 0.5},
            previous_programs=[
                {
                    "id": "prev1",
                    "code": "def prev1():\n    pass\n",
                    "metrics": {"combined_score": 0.4},
                }
            ],
            top_programs=[
                {
                    "id": "top1",
                    "code": "def top1():\n    pass\n",
                    "metrics": {"combined_score": 0.6},
                }
            ],
        )


class OpenEvolveIslandSelectionSemantics:
    """Thin adapter for the upstream ``sample_from_island`` claims."""

    @staticmethod
    def assert_controller_uses_stock_island_runtime(test: unittest.TestCase, harness):
        test.assertIsInstance(harness.controller.db, IslandsStore)
        test.assertIsInstance(harness.controller.substrate.policy, StockOpenEvolveSelection)

    @staticmethod
    def assert_requested_island_is_parent_and_inspiration_scope(
        test: unittest.TestCase,
        harness: NoemaControllerHarness,
        *,
        target_scope: int,
    ) -> None:
        random.seed(42)
        seen_parent_ids = set()
        for _ in range(25):
            selection = harness.select(target_scope=target_scope, num_inspirations=2)
            seen_parent_ids.add(selection.parent.id)
            test.assertEqual(selection.target_scope, target_scope)
            test.assertEqual(selection.parent.metadata.get("island"), target_scope)
            for inspiration in selection.inspirations:
                test.assertEqual(inspiration.metadata.get("island"), target_scope)

        test.assertTrue(seen_parent_ids)
        test.assertTrue(
            all(program_id.startswith(f"i{target_scope}_") for program_id in seen_parent_ids)
        )

    @staticmethod
    def assert_controller_samples_iteration_target_scopes(
        test: unittest.TestCase,
        harness: NoemaControllerHarness,
        expected_scopes: List[int],
    ) -> None:
        test.assertEqual(harness.record_iteration_sample_scopes(), expected_scopes)


class OpenEvolvePromptSamplerSemantics:
    """Thin adapter for the upstream PromptSampler smoke claim."""

    @staticmethod
    def assert_controller_builds_deterministic_openevolve_prompt(
        test: unittest.TestCase,
        harness: NoemaControllerHarness,
    ) -> None:
        prompt = harness.build_prompt()
        prompt_again = harness.build_prompt()

        test.assertIn("system", prompt)
        test.assertIn("user", prompt)
        test.assertIn("def test():", prompt["user"])
        test.assertIn("0.5", prompt["user"])
        test.assertFalse(harness.controller.config.prompt.use_template_stochasticity)
        test.assertEqual(prompt, prompt_again)


class TestControllerBackedOpenEvolveIslandSelection(unittest.TestCase):
    def test_controller_stock_selection_samples_parent_and_inspirations_from_requested_island(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = NoemaControllerHarness(tmp, _islands_stock_config())
            harness.populate_islands()

            OpenEvolveIslandSelectionSemantics.assert_controller_uses_stock_island_runtime(
                self, harness
            )
            OpenEvolveIslandSelectionSemantics.assert_requested_island_is_parent_and_inspiration_scope(
                self, harness, target_scope=1
            )

    def test_controller_iteration_sampling_uses_the_iteration_target_scope_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = NoemaControllerHarness(
                tmp,
                _islands_stock_config(num_islands=3, max_iterations=4),
            )

            OpenEvolveIslandSelectionSemantics.assert_controller_samples_iteration_target_scopes(
                self, harness, [0, 1, 2, 0]
            )


class TestControllerBackedOpenEvolvePromptSampler(unittest.TestCase):
    def test_controller_prompt_sampler_builds_openevolve_prompt_with_noema_stochasticity_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = NoemaControllerHarness(tmp, _islands_stock_config())

            OpenEvolvePromptSamplerSemantics.assert_controller_builds_deterministic_openevolve_prompt(
                self, harness
            )


if __name__ == "__main__":
    unittest.main()
