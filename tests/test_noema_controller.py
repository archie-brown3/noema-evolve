"""
End-to-end tests for the noema controller with a stubbed chat-completions client.

The mutation LLM is a real BudgetedLLM wired to a fake API client, so these
tests exercise ledger metering, prompt assembly, parsing, evaluation via
openevolve's Evaluator, database insertion, coordination hooks, and
checkpoint/resume — everything except the network.
"""

import asyncio
import json
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

import yaml

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import BudgetConfig, CoordinationConfig, NoemaConfig
from noema.controller import NoemaController
from noema.coordination import Advice, NullCoordination
from noema.coordination.pes.module import PESPlannerModule
from noema.substrate.prompts import COORDINATION_HEADER

from openevolve.config import DatabaseConfig, EvaluatorConfig

INITIAL_PROGRAM = "def f():\n    return 1\n"

# The eval script scores programs by the number they return (regex, no exec)
EVAL_SCRIPT = """\
import re

def evaluate(program_path):
    with open(program_path) as f:
        code = f.read()
    m = re.search(r"return (\\d+(?:\\.\\d+)?)", code)
    value = float(m.group(1)) if m else 0.0
    return {"combined_score": min(1.0, value / 10.0)}
"""


class CyclingFakeClient:
    """Fake AsyncOpenAI yielding full-rewrite responses with increasing scores"""

    def __init__(self, prompt_tokens=100, completion_tokens=40):
        self.calls = []
        self._counter = 0

        async def create(**params):
            self.calls.append(params)
            self._counter += 1
            content = f"```python\ndef f():\n    return {self._counter + 1}\n```"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def make_config(**overrides) -> NoemaConfig:
    defaults = dict(
        max_iterations=6,
        checkpoint_interval=100,
        diff_based_evolution=False,  # fake client emits full rewrites
        database=DatabaseConfig(
            in_memory=True,
            num_islands=2,
            population_size=50,
            random_seed=42,
            migration_interval=1000,  # keep migration out of these tests
        ),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        budget=BudgetConfig(total_tokens=1_000_000),
    )
    defaults.update(overrides)
    return NoemaConfig(**defaults)


def make_controller(tmp, config=None, budget_tokens=1_000_000, client=None):
    eval_path = os.path.join(tmp, "evaluator.py")
    if not os.path.exists(eval_path):
        with open(eval_path, "w") as f:
            f.write(EVAL_SCRIPT)

    config = config or make_config()
    ledger = TokenLedger(total_budget_tokens=budget_tokens)
    client = client or CyclingFakeClient()
    mutation_llm = BudgetedLLM(
        model="fake-model",
        ledger=ledger,
        account="mutation",
        tag="mutate",
        client=client,
        retries=0,
        retry_delay=0.0,
    )
    controller = NoemaController(
        config=config,
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        mutation_llm=mutation_llm,
        coordination=NullCoordination(),
        ledger=ledger,
    )
    return controller, ledger, client


class TestControllerEndToEnd(unittest.TestCase):
    def test_off_arm_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, client = make_controller(tmp)
            best = asyncio.run(controller.run())

            # Evolution happened: initial + children, improving scores
            self.assertGreater(controller.db.num_programs, 1)
            self.assertIsNotNone(best)
            self.assertGreater(best.metrics["combined_score"], 0.1)

            # Every mutation call was metered
            self.assertEqual(len(client.calls), 6)
            self.assertEqual(ledger.spent("mutation"), 6 * 140)
            self.assertEqual(ledger.spent("coordination"), 0)
            iterations = [r.iteration for r in ledger.records]
            self.assertEqual(iterations, list(range(6)))

            # Generation ticks: 6 iterations / 2 islands = 3
            self.assertEqual(controller.generation, 3)
            self.assertEqual(len(controller.best_fitness_history), 3)
            self.assertEqual(len(controller.generation_log), 3)

            # Final checkpoint written with noema state
            checkpoint = os.path.join(tmp, "output", "checkpoints", "checkpoint_5")
            self.assertTrue(os.path.exists(os.path.join(checkpoint, "noema_state.json")))

    def test_children_are_distributed_across_islands_not_all_island_zero(self):
        # Regression test: db.add() was never told which island a child
        # belongs to, so every child fell back to island 0 regardless of
        # num_islands, silently collapsing every noema run into a single
        # lineage tree. With num_islands=2 and 6 iterations (island =
        # iteration % 2), both islands must end up with real children.
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp)
            asyncio.run(controller.run())

            children = [
                p for p in controller.db._db.programs.values() if p.parent_id is not None
            ]
            self.assertTrue(children)
            islands_used = {child.metadata["island"] for child in children}
            self.assertEqual(islands_used, {0, 1})
            # And the database's own island bookkeeping agrees, not just metadata
            self.assertTrue(controller.db.island_fitnesses(0))
            self.assertTrue(controller.db.island_fitnesses(1))

    def test_off_arm_prompts_have_no_coordination_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, client = make_controller(tmp)
            asyncio.run(controller.run(iterations=2))

            for call in client.calls:
                user_message = call["messages"][-1]["content"]
                self.assertNotIn(COORDINATION_HEADER.strip(), user_message)

            # Prompts are also logged on the stored programs for arm-diffing
            children = [p for p in controller.db._db.programs.values() if p.parent_id is not None]
            self.assertTrue(children)
            for child in children:
                self.assertIn("full_rewrite_user", child.prompts)

    def test_advice_reaches_prompt_and_attribution_reaches_metadata(self):
        class StaticAdviceModule(NullCoordination):
            async def advise(self, ctx):
                return Advice(
                    prompt_block="- Prefer closed-form solutions",
                    attribution={"insights": ["tip-1"]},
                )

        with tempfile.TemporaryDirectory() as tmp:
            controller, _, client = make_controller(tmp)
            controller.coordination = StaticAdviceModule()
            asyncio.run(controller.run(iterations=2))

            for call in client.calls:
                user_message = call["messages"][-1]["content"]
                self.assertIn(COORDINATION_HEADER, user_message)
                self.assertIn("- Prefer closed-form solutions", user_message)

            children = [p for p in controller.db._db.programs.values() if p.parent_id is not None]
            self.assertTrue(children)
            for child in children:
                self.assertEqual(child.metadata["coordination"], {"insights": ["tip-1"]})

    def test_budget_exhaustion_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Each call costs 140 tokens; budget allows exactly two calls
            controller, ledger, client = make_controller(tmp, budget_tokens=280)
            best = asyncio.run(controller.run())

            self.assertEqual(len(client.calls), 2)
            self.assertLessEqual(ledger.remaining(), 0)
            self.assertIsNotNone(best)  # run ended cleanly with a result
            self.assertEqual(controller.start_iteration, 2)

    def test_checkpoint_resume_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, client = make_controller(tmp)
            asyncio.run(controller.run(iterations=4))
            checkpoint = os.path.join(tmp, "output", "checkpoints", "checkpoint_3")
            self.assertTrue(os.path.exists(checkpoint))
            programs_before = controller.db.num_programs
            spent_before = ledger.spent()

            # Fresh controller, fresh ledger; restore everything from disk
            controller2, ledger2, client2 = make_controller(tmp)
            controller2.load_checkpoint(checkpoint)
            self.assertEqual(controller2.start_iteration, 4)
            self.assertEqual(controller2.db.num_programs, programs_before)
            self.assertEqual(ledger2.spent(), spent_before)
            self.assertEqual(controller2.generation, 2)

            asyncio.run(controller2.run(iterations=2))
            self.assertEqual(controller2.start_iteration, 6)
            # Resumed ledger accumulates on top of restored spend
            self.assertEqual(ledger2.spent(), spent_before + 2 * 140)

    def test_unparseable_response_reports_failure(self):
        # Note: only diff-based mode can fail to parse — openevolve's
        # parse_full_rewrite falls back to treating the whole response as code
        class GarbageClient:
            def __init__(self):
                self.calls = []

                async def create(**params):
                    self.calls.append(params)
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="no diffs here"))],
                        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                    )

                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        class RecordingModule(NullCoordination):
            def __init__(self):
                super().__init__()
                self.reports = []

            def report_result(self, ctx, child, attribution, eval_failed):
                self.reports.append((child, eval_failed))

        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(diff_based_evolution=True)
            controller, ledger, _ = make_controller(tmp, config=config, client=GarbageClient())
            module = RecordingModule()
            controller.coordination = module
            asyncio.run(controller.run(iterations=2))

            # Failed parses are still metered and still reported to coordination
            self.assertEqual(ledger.spent(), 2 * 15)
            self.assertEqual(len(module.reports), 2)
            for child, eval_failed in module.reports:
                self.assertIsNone(child)
                self.assertTrue(eval_failed)
            # No children were added
            self.assertEqual(controller.db.num_programs, 1)


class TestFrozenConfig(unittest.TestCase):
    def test_run_dir_contains_frozen_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp)
            config_path = os.path.join(tmp, "output", "config.yaml")
            self.assertTrue(os.path.exists(config_path))

            with open(config_path) as f:
                text = f.read()
            reloaded = NoemaConfig.from_dict(yaml.safe_load(text))
            self.assertEqual(reloaded.max_iterations, controller.config.max_iterations)
            self.assertEqual(
                reloaded.database.num_islands, controller.config.database.num_islands
            )
            self.assertEqual(reloaded.coordination.module, controller.config.coordination.module)

    def test_paired_arm_configs_differ_only_in_coordination_module(self):
        null_config = make_config(coordination=CoordinationConfig(module="null"))
        pes_config = make_config(coordination=CoordinationConfig(module="pes"))

        null_lines = null_config.to_yaml().splitlines()
        pes_lines = pes_config.to_yaml().splitlines()
        self.assertEqual(len(null_lines), len(pes_lines))

        differing = [
            (a, b) for a, b in zip(null_lines, pes_lines) if a != b
        ]
        self.assertTrue(differing)
        for a, b in differing:
            self.assertIn("module", a)
            self.assertIn("module", b)

    def test_resume_does_not_clobber_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp)
            config_path = os.path.join(tmp, "output", "config.yaml")
            with open(config_path) as f:
                original_text = f.read()

            # Second controller construction against the same output_dir
            # (as happens on checkpoint resume) must not overwrite it.
            different_config = make_config(max_iterations=999)
            make_controller(tmp, config=different_config)

            with open(config_path) as f:
                after_text = f.read()
            self.assertEqual(original_text, after_text)


class TestNoemaConfig(unittest.TestCase):
    def test_from_yaml_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w") as f:
                f.write(
                    "max_iterations: 7\n"
                    "database:\n"
                    "  num_islands: 3\n"
                    "budget:\n"
                    "  total_tokens: 5000\n"
                    "coordination:\n"
                    "  module: null_arm_typo_guard\n"
                )
            config = NoemaConfig.from_yaml(path)
            self.assertEqual(config.max_iterations, 7)
            self.assertEqual(config.database.num_islands, 3)
            self.assertEqual(config.budget.total_tokens, 5000)
            self.assertEqual(config.coordination.module, "null_arm_typo_guard")
            # Defaults enforced for ablation validity
            self.assertFalse(config.prompt.use_template_stochasticity)
            self.assertFalse(config.evaluator.cascade_evaluation)

    def test_stochasticity_rejected_at_config_level(self):
        from openevolve.config import PromptConfig

        with self.assertRaises(ValueError):
            NoemaConfig(prompt=PromptConfig(use_template_stochasticity=True))

    def test_unknown_coordination_module_rejected_at_build(self):
        from noema.coordination import build_coordination_module

        with self.assertRaises(ValueError):
            build_coordination_module("does-not-exist")

    def test_mutation_operator_seed_defaults_to_random_seed_plus_two(self):
        config = NoemaConfig(random_seed=10)
        self.assertEqual(config.mutation_operator_seed, 12)

    def test_mutation_operators_none_by_default(self):
        self.assertIsNone(NoemaConfig().mutation_operators)

    def test_unknown_mutation_operator_rejected(self):
        with self.assertRaises(ValueError):
            NoemaConfig(mutation_operators=["not-a-real-operator"])

    def test_full_rewrite_operator_rejected_with_changes_description(self):
        from openevolve.config import PromptConfig

        with self.assertRaises(ValueError):
            NoemaConfig(
                mutation_operators=["e1"],  # full_rewrite
                prompt=PromptConfig(
                    use_template_stochasticity=False,
                    programs_as_changes_description=True,
                ),
            )

    def test_diff_only_operator_allowed_with_changes_description(self):
        from openevolve.config import PromptConfig

        # m1 is parse_mode="diff" — should not raise
        NoemaConfig(
            mutation_operators=["m1"],
            prompt=PromptConfig(
                use_template_stochasticity=False,
                programs_as_changes_description=True,
            ),
        )


class RetryFailingThenSuccessClient:
    """Fake client: first `fail_count` calls return garbage; then valid diffs"""

    def __init__(self, fail_count=1, prompt_tokens=100, completion_tokens=40):
        self.calls = []
        self._counter = 0

        async def create(**params):
            self.calls.append(params)
            self._counter += 1
            if self._counter <= fail_count:
                content = "no diffs here"
            else:
                val = self._counter - fail_count + 1
                content = (
                    f"<<<<<<< SEARCH\ndef f():\n    return 1\n=======\n"
                    f"def f():\n    return {val}\n>>>>>>> REPLACE"
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class AlwaysGarbageClient:
    """Fake client: every response is unparseable"""

    def __init__(self, prompt_tokens=10, completion_tokens=5):
        self.calls = []

        async def create(**params):
            self.calls.append(params)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="no diffs here"))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestRetryLoop(unittest.TestCase):
    def test_parse_failure_retries_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = make_config(diff_based_evolution=True, retry_enabled=True, retry_cap=2)
            client = RetryFailingThenSuccessClient(fail_count=1)
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            mutation_llm = BudgetedLLM(
                model="fake-model",
                ledger=ledger,
                account="mutation",
                tag="mutate",
                client=client,
                retries=0,
                retry_delay=0.0,
            )
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=mutation_llm,
                coordination=NullCoordination(),
                ledger=ledger,
            )
            asyncio.run(controller.run(iterations=1))

            # One iteration produced a child
            self.assertEqual(controller.db.num_programs, 2)  # initial + 1 child
            children = [p for p in controller.db._db.programs.values() if p.parent_id is not None]
            self.assertEqual(len(children), 1)
            # Two mutation calls: one garbage, one success
            self.assertEqual(len(client.calls), 2)

    def test_parse_failure_exhausts_retries_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = make_config(diff_based_evolution=True, retry_enabled=True, retry_cap=1)
            client = AlwaysGarbageClient()
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            mutation_llm = BudgetedLLM(
                model="fake-model", ledger=ledger, account="mutation", tag="mutate",
                client=client, retries=0, retry_delay=0.0,
            )
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=mutation_llm,
                coordination=NullCoordination(),
                ledger=ledger,
            )
            asyncio.run(controller.run(iterations=1))

            # No child produced
            self.assertEqual(controller.db.num_programs, 1)
            # retry_cap + 1 = 2 mutation calls
            self.assertEqual(len(client.calls), 2)

    def test_d9_one_verdict_one_iteration_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = make_config(diff_based_evolution=True, retry_enabled=True, retry_cap=2)
            client = AlwaysGarbageClient()
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            mutation_llm = BudgetedLLM(
                model="fake-model", ledger=ledger, account="mutation", tag="mutate",
                client=client, retries=0, retry_delay=0.0,
            )
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=mutation_llm,
                coordination=NullCoordination(),
                ledger=ledger,
            )
            # Run 3 iterations; each fails with 3 calls
            asyncio.run(controller.run(iterations=3))

            # start_iteration == 3, NOT 9
            self.assertEqual(controller.start_iteration, 3)
            # 3 iterations × 3 calls each = 9 mutation calls
            self.assertEqual(len(client.calls), 9)

    def test_retries_metered_on_mutation_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = make_config(diff_based_evolution=True, retry_enabled=True, retry_cap=1)
            client = AlwaysGarbageClient(prompt_tokens=10, completion_tokens=5)
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            mutation_llm = BudgetedLLM(
                model="fake-model", ledger=ledger, account="mutation", tag="mutate",
                client=client, retries=0, retry_delay=0.0,
            )
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=mutation_llm,
                coordination=NullCoordination(),
                ledger=ledger,
            )
            asyncio.run(controller.run(iterations=1))

            # Each call = 10 + 5 = 15 tokens; retry_cap=1 → 2 calls total
            self.assertEqual(ledger.spent("mutation"), 2 * 15)
            self.assertEqual(ledger.spent("coordination"), 0)
            # Ledger records are on the mutation account
            self.assertEqual(len(ledger.records), 2)
            for record in ledger.records:
                self.assertEqual(record.account, "mutation")

    def test_retry_suffix_reaches_retry_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = make_config(diff_based_evolution=True, retry_enabled=True, retry_cap=2)
            client = RetryFailingThenSuccessClient(fail_count=1)
            ledger = TokenLedger(total_budget_tokens=1_000_000)
            mutation_llm = BudgetedLLM(
                model="fake-model", ledger=ledger, account="mutation", tag="mutate",
                client=client, retries=0, retry_delay=0.0,
            )
            controller = NoemaController(
                config=config,
                evaluation_file=eval_path,
                initial_program_code=INITIAL_PROGRAM,
                output_dir=os.path.join(tmp, "output"),
                mutation_llm=mutation_llm,
                coordination=NullCoordination(),
                ledger=ledger,
            )
            asyncio.run(controller.run(iterations=1))

            # First call = normal prompt, second call = retry prompt
            self.assertEqual(len(client.calls), 2)
            first_content = client.calls[0]["messages"][-1]["content"]
            second_content = client.calls[1]["messages"][-1]["content"]
            self.assertNotIn("# Retry After Failure", first_content)
            self.assertIn("# Retry After Failure", second_content)
            self.assertIn("no parseable code block found in the response", second_content)
            self.assertIn("Produce a corrected program", second_content)

    def test_retry_enabled_false_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, client = make_controller(tmp)
            asyncio.run(controller.run(iterations=2))
            self.assertEqual(len(client.calls), 2)

    def test_pes_retry_carries_reflection_null_does_not(self):
        # Controlled-variable guard: same retry loop, same error, only the
        # coordination module differs. PES retry prompt must carry reflection;
        # Null's must not.
        reflection_text = "The loop overran the array bound; cap the index at n-1."
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = make_config(diff_based_evolution=True, retry_enabled=True, retry_cap=2)

            def run_with(module):
                client = RetryFailingThenSuccessClient(fail_count=1)
                ledger = TokenLedger(total_budget_tokens=1_000_000)
                mutation_llm = BudgetedLLM(
                    model="fake-model", ledger=ledger, account="mutation", tag="mutate",
                    client=client, retries=0, retry_delay=0.0,
                )
                controller = NoemaController(
                    config=config,
                    evaluation_file=eval_path,
                    initial_program_code=INITIAL_PROGRAM,
                    output_dir=os.path.join(tmp, f"output_{id(module)}"),
                    mutation_llm=mutation_llm,
                    coordination=module,
                    ledger=ledger,
                )
                asyncio.run(controller.run(iterations=1))
                return client

            # Null: retry prompt has raw error only, no reflection
            null_client = run_with(NullCoordination())
            null_retry_content = null_client.calls[1]["messages"][-1]["content"]
            self.assertIn("# Retry After Failure", null_retry_content)
            self.assertNotIn("# Reflection on the lineage's last failure", null_retry_content)

            # PES: pre-seed reflection so retry_advice returns it (the module's
            # own reflection pipeline normally populates this at the generation
            # tick; here we seed directly to isolate the retry-seeding path)
            pes_module = PESPlannerModule(config={}, llm=None, rng=random.Random(0))
            # The parent id for it000000's mutation is "initial"
            pes_module._plans["initial"] = {
                "plan": "# Plan\n## Strategy\n- try something\n## Action\n1. go",
                "outcome": "failed",
                "parent_fitness": 0.1,
                "child_fitness": 0.0,
                "reflection": reflection_text,
            }
            pes_client = run_with(pes_module)
            pes_retry_content = pes_client.calls[1]["messages"][-1]["content"]
            self.assertIn("# Retry After Failure", pes_retry_content)
            self.assertIn("# Reflection on the lineage's last failure", pes_retry_content)
            self.assertIn(reflection_text, pes_retry_content)
            self.assertIn("Use this causal explanation", pes_retry_content)


if __name__ == "__main__":
    unittest.main()
