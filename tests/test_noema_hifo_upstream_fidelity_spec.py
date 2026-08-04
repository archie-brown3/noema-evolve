"""Controller-backed HiFo upstream fidelity specs for task 0188.

HiFo-Prompt does not ship pytest-style unit tests for the prompt guidance seam
we care about, so the first assertion in this file treats the pinned upstream
source literals as the green upstream oracle.  The second assertion ports that
contract through a constructed ``NoemaController`` and a thin Noema adapter.
"""

from __future__ import annotations

import ast
import asyncio
import os
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import CoordinationConfig, NoemaConfig
from noema.controller import NoemaController
from noema.coordination.hifo.module import INSIGHTS_PREFIX
from tests.test_noema_controller import (
    EVAL_SCRIPT,
    INITIAL_PROGRAM,
    CyclingFakeClient,
    make_config,
)


UPSTREAM_HIFO_ROOT = Path(
    os.environ.get("NOEMA_HIFO_UPSTREAM", "/Users/archiebrown/HiFo-Prompt")
)
UPSTREAM_INSIGHT_POOL = (
    UPSTREAM_HIFO_ROOT / "hifo/src/hifo/methods/hifo/insight_pool.py"
)
UPSTREAM_EVOLUTION = UPSTREAM_HIFO_ROOT / "hifo/src/hifo/methods/hifo/hifo_evolution.py"


class UpstreamHiFoSource:
    """Source-backed oracle for the released HiFo-Prompt guidance contract."""

    def __init__(self, root: Path = UPSTREAM_HIFO_ROOT):
        self.root = root
        self.insight_pool = root / "hifo/src/hifo/methods/hifo/insight_pool.py"
        self.evolution = root / "hifo/src/hifo/methods/hifo/hifo_evolution.py"

    def require(self) -> None:
        if not self.insight_pool.exists() or not self.evolution.exists():
            raise unittest.SkipTest(
                f"HiFo upstream checkout not found at {self.root}; "
                "set NOEMA_HIFO_UPSTREAM to run this fidelity oracle"
            )

    def default_initial_tips(self) -> List[str]:
        self.require()
        tree = ast.parse(self.insight_pool.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "default_initial_tips"
                for target in node.targets
            ):
                continue
            tips = ast.literal_eval(node.value)
            if not isinstance(tips, list):
                raise AssertionError("upstream default_initial_tips is not a list")
            return tips
        raise AssertionError("upstream default_initial_tips literal was not found")

    def assert_i1_suffix_literals(self, test: unittest.TestCase) -> None:
        self.require()
        text = self.evolution.read_text()
        test.assertIn("Consider these successful design principles I've observed recently:", text)
        test.assertIn("For this task, please pay special attention to:", text)
        test.assertIn(
            "Strike a balance between novel ideas and proven effective techniques.",
            text,
        )


@dataclass
class HiFoControllerHarness:
    """Constructs Noema's controller, then exposes the generated mutation prompt."""

    tmp: str
    config: NoemaConfig
    controller: Any = field(init=False)
    ledger: TokenLedger = field(init=False)
    client: CyclingFakeClient = field(init=False)

    def __post_init__(self) -> None:
        eval_path = os.path.join(self.tmp, "evaluator.py")
        with open(eval_path, "w") as handle:
            handle.write(EVAL_SCRIPT)

        self.ledger = TokenLedger(total_budget_tokens=1_000_000)
        self.client = CyclingFakeClient()
        mutation_llm = BudgetedLLM(
            model="fake-model",
            ledger=self.ledger,
            account="mutation",
            tag="mutate",
            client=self.client,
            retries=0,
            retry_delay=0.0,
        )
        self.controller = NoemaController(
            config=self.config,
            evaluation_file=eval_path,
            initial_program_code=INITIAL_PROGRAM,
            output_dir=os.path.join(self.tmp, "output"),
            mutation_llm=mutation_llm,
            ledger=self.ledger,
        )

    def run_one_iteration_prompt(self) -> str:
        asyncio.run(self.controller.run(iterations=1))
        return self.client.calls[0]["messages"][-1]["content"]


class HiFoSeedPromptSemantics:
    """Thin adapter from upstream HiFo literals to Noema's controller prompt."""

    @staticmethod
    def assert_controller_uses_upstream_seed_tips_and_i1_suffix(
        test: unittest.TestCase,
        harness: HiFoControllerHarness,
        upstream: UpstreamHiFoSource,
    ) -> None:
        prompt = harness.run_one_iteration_prompt()
        upstream_tips = upstream.default_initial_tips()

        test.assertEqual(
            list(harness.controller.coordination.insight_pool.tips),
            upstream_tips,
        )
        test.assertIn("# Coordination Guidance", prompt)
        test.assertIn(INSIGHTS_PREFIX, prompt)
        for tip in upstream_tips:
            test.assertIn(f"- {tip}", prompt)
        test.assertIn("For this task, please pay special attention to:", prompt)
        test.assertIn(
            "Strike a balance between novel ideas and proven effective techniques.",
            prompt,
        )
        test.assertEqual(harness.ledger.spent("coordination"), 0)


def _hifo_controller_config() -> NoemaConfig:
    config = make_config(
        max_iterations=1,
        coordination=CoordinationConfig(
            module="hifo",
            params={
                "tips_per_prompt": 5,
                "extraction_probability": 0.0,
            },
        ),
    )
    # The controller builds the coordination LLM seat when it builds HiFo from
    # config. Extraction is disabled in this test, so the client is never called;
    # this dummy value only lets the SDK construct without reading live creds.
    config.llm.coordination.api_key = "test-key"
    return config


class TestUpstreamHiFoPromptGuidanceOracle(unittest.TestCase):
    def test_upstream_hifo_default_tips_and_i1_suffix_literals_are_available(self):
        upstream = UpstreamHiFoSource()

        tips = upstream.default_initial_tips()

        self.assertEqual(len(tips), 5)
        self.assertTrue(all(isinstance(tip, str) and tip for tip in tips))
        upstream.assert_i1_suffix_literals(self)


class TestControllerBackedHiFoPromptGuidance(unittest.TestCase):
    def test_controller_hifo_prompt_uses_upstream_seed_tips_and_i1_suffix(self):
        upstream = UpstreamHiFoSource()
        upstream.assert_i1_suffix_literals(self)

        with tempfile.TemporaryDirectory() as tmp:
            harness = HiFoControllerHarness(
                tmp,
                _hifo_controller_config(),
            )

            HiFoSeedPromptSemantics.assert_controller_uses_upstream_seed_tips_and_i1_suffix(
                self,
                harness,
                upstream,
            )


if __name__ == "__main__":
    unittest.main()
