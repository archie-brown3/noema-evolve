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
import re
import tempfile
import unittest
from types import SimpleNamespace

import yaml

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    NoemaConfig,
    SelectionConfig,
    SubstrateConfig,
)
from noema.controller import NoemaController
from noema.coordination import (
    DEPRECATED_ALIASES,
    MODULE_REGISTRY,
    Advice,
    NullCoordination,
    Outcome,
    SamplingRequest,
    build_coordination_module,
)
from noema.coordination.base import GenerationContext
from noema.coordination.pes.arms import PESCustomModule
from noema.coordination.pes.module import PESPlannerModule
from noema.prompts import COORDINATION_HEADER
from noema.views import ProgramView

from openevolve.config import DatabaseConfig, EvaluatorConfig
from openevolve.database import Program

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

    def test_evolution_trace_includes_iteration_ledger_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, _ = make_controller(tmp)
            asyncio.run(controller.run(iterations=2))

            trace_path = os.path.join(tmp, "output", "evolution_trace.jsonl")
            self.assertTrue(os.path.exists(trace_path))
            with open(trace_path) as f:
                traces = [json.loads(line) for line in f]

            self.assertEqual(len(traces), 2)
            first = traces[0]
            self.assertEqual(first["iteration"], 0)
            self.assertEqual(first["metadata"]["changes"], "Full rewrite")
            self.assertEqual(first["metadata"]["operator"], "legacy")
            token_ledger = first["metadata"]["token_ledger"]
            self.assertEqual(token_ledger["spent_total"], 140)
            self.assertEqual(token_ledger["spent_by_account"]["mutation"], 140)
            self.assertEqual(token_ledger["calls"][0]["iteration"], 0)
            self.assertEqual(token_ledger["calls"][0]["prompt_tokens"], 100)
            self.assertEqual(token_ledger["calls"][0]["completion_tokens"], 40)
            self.assertEqual(token_ledger["calls"][0]["tag"], "mutate")
            self.assertEqual(ledger.records[0].iteration, 0)

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

            def report_result(
                self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED
            ):
                self.reports.append((child, eval_failed, outcome))

        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(diff_based_evolution=True)
            controller, ledger, _ = make_controller(tmp, config=config, client=GarbageClient())
            module = RecordingModule()
            controller.coordination = module
            asyncio.run(controller.run(iterations=2))

            # Failed parses are still metered and still reported to coordination
            self.assertEqual(ledger.spent(), 2 * 15)
            self.assertEqual(len(module.reports), 2)
            for child, eval_failed, outcome in module.reports:
                self.assertIsNone(child)
                self.assertTrue(eval_failed)
                # Unparseable response -> NO_PROGRAM, not EVAL_ERROR (task 0090)
                self.assertEqual(outcome, Outcome.NO_PROGRAM)
            # No children were added
            self.assertEqual(controller.db.num_programs, 1)


class TestReportResultOutcome(unittest.TestCase):
    """report_result's outcome discriminator (task 0090).

    The controller must classify each iteration's credit-assignment call:
    ACCEPTED for a real evaluated child, NO_PROGRAM for unparseable/over-length,
    EVAL_ERROR for applyable code that failed at evaluation. NO_PROGRAM is
    covered by TestFailedMutationsAreReported above.
    """

    class _Recorder(NullCoordination):
        def __init__(self):
            super().__init__()
            self.outcomes = []

        def report_result(
            self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED
        ):
            self.outcomes.append(outcome)

    def _run(self, evaluator=None, config=None, client=None):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, _ = make_controller(
                tmp, config=config or make_config(max_iterations=2), client=client
            )
            if evaluator is not None:
                controller.evaluator = evaluator
            module = self._Recorder()
            controller.coordination = module
            asyncio.run(controller.run(iterations=2))
            return module.outcomes, controller

    def test_successful_iteration_reports_accepted(self):
        # CyclingFakeClient emits parseable rewrites the default evaluator scores.
        outcomes, controller = self._run()
        self.assertEqual(outcomes, [Outcome.ACCEPTED, Outcome.ACCEPTED])
        self.assertGreater(controller.db.num_programs, 1)

    def test_applyable_code_that_fails_evaluation_reports_eval_error(self):
        class ErroringEvaluator:
            async def evaluate_program(self, code, program_id):
                return {"error": "evaluation blew up"}

            def get_pending_artifacts(self, program_id):
                return {"stderr": "Traceback: boom"}

        # Parseable rewrites (so NOT no_program) that then error at evaluation.
        outcomes, controller = self._run(evaluator=ErroringEvaluator())
        self.assertEqual(outcomes, [Outcome.EVAL_ERROR, Outcome.EVAL_ERROR])
        # eval_error children are not added to the population
        self.assertEqual(controller.db.num_programs, 1)

    def test_outcome_is_json_safe_and_matches_its_value(self):
        # str-enum: it serializes verbatim into the run log / attribution.
        self.assertEqual(Outcome.EVAL_ERROR, "eval_error")
        self.assertEqual(json.dumps(Outcome.ACCEPTED), '"accepted"')


class TestExistingArmsIgnoreOutcome(unittest.TestCase):
    """Behaviour-identity: passing outcome must not change null/hifo/pes.

    The whole safety argument for touching base.py is that outcome is additive
    and keyword-only, so an arm that does not read it is byte-for-byte unchanged.
    This asserts that directly, per arm, rather than trusting it.
    """

    def _make_ctx(self, child_present):
        from noema.coordination.base import GenerationContext
        parent = ProgramView(id="p", code="def f():\n    return 1\n", fitness=0.5,
                             metrics={"combined_score": 0.5})
        child = (
            ProgramView(id="c", code="def f():\n    return 2\n", fitness=0.4,
                       metrics={"combined_score": 0.4})
            if child_present else None
        )
        return GenerationContext(iteration=1, generation=0, scope_id=0, parent=parent,
                                 local_population=None, global_population=None), child

    def test_each_arm_unchanged_across_every_outcome_value(self):
        for key in ("null", "hifo", "pes-custom"):
            for outcome in Outcome:
                child_present = outcome == Outcome.ACCEPTED
                # A module built fresh for each call so state is comparable.
                base_mod = build_coordination_module(key, {}, llm=None)
                test_mod = build_coordination_module(key, {}, llm=None)
                ctx_a, child_a = self._make_ctx(child_present)
                ctx_b, child_b = self._make_ctx(child_present)
                attribution = {"insights": [], "plan": None}
                # Default call (as if outcome had never been added)...
                base_mod.report_result(ctx_a, child_a, dict(attribution),
                                       eval_failed=not child_present)
                # ...vs an explicit outcome. State must be identical.
                test_mod.report_result(ctx_b, child_b, dict(attribution),
                                       eval_failed=not child_present, outcome=outcome)
                self.assertEqual(
                    base_mod.state_dict(), test_mod.state_dict(),
                    f"{key} changed when outcome={outcome} was passed",
                )


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

    def test_retry_on_defaults_to_failure_and_rejects_unknown(self):
        self.assertEqual(NoemaConfig().retry_on, "failure")
        with self.assertRaises(ValueError):
            NoemaConfig(retry_on="always")

    def test_retry_on_survives_yaml_round_trip(self):
        config = NoemaConfig(retry_on="non_improvement", retry_enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w") as f:
                f.write(config.to_yaml())
            restored = NoemaConfig.from_yaml(path)
        self.assertEqual(restored.retry_on, "non_improvement")
        self.assertTrue(restored.retry_enabled)

    def test_selection_policy_defaults_and_validation(self):
        config = NoemaConfig()
        self.assertEqual(config.substrate.kind, "islands")
        self.assertEqual(config.selection.policy, "substrate_default")
        with self.assertRaises(ValueError):
            NoemaConfig(selection=SelectionConfig(policy="not-a-policy"))


class TestSubstrateRuntimeControllerContract(unittest.TestCase):
    def test_tree_uct_runs_offline_with_deep_lineage_and_checkpoint_resume(self):
        from noema.selection.uct import UCTSelectionPolicy
        from noema.tree import TreeStore

        config = make_config(
            max_iterations=5,
            substrate=SubstrateConfig(kind="tree", steps_per_generation=2),
            selection=SelectionConfig(
                policy="uct",
                seed=123,
                initial_exploration=0.1,
                widening_alpha=0.5,
            ),
        )

        class InlineEvaluator:
            async def evaluate_program(self, code, program_id):
                match = re.search(r"return (\d+(?:\.\d+)?)", code)
                value = float(match.group(1)) if match else 0.0
                return {"combined_score": min(1.0, value / 10.0)}

            def get_pending_artifacts(self, program_id):
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, client = make_controller(tmp, config=config)
            controller.evaluator = InlineEvaluator()
            best = asyncio.run(controller.run(iterations=5))

            self.assertIsInstance(controller.db, TreeStore)
            self.assertIsInstance(controller.substrate.policy, UCTSelectionPolicy)
            self.assertEqual(controller.db.num_programs, 6)
            self.assertEqual(len(client.calls), 5)
            self.assertIsNotNone(best)
            self.assertGreaterEqual(
                max(item.generation for item in controller.db.population()), 2
            )
            self.assertEqual(controller.generation, 2)
            self.assertEqual(controller.substrate.policy.tokens_spent, ledger.spent())

            checkpoint = os.path.join(
                tmp, "output", "checkpoints", "checkpoint_4"
            )
            resumed, resumed_ledger, _ = make_controller(tmp, config=config)
            resumed.load_checkpoint(checkpoint)
            expected = controller.substrate.select(
                target_scope=None, num_inspirations=2
            )
            actual = resumed.substrate.select(
                target_scope=None, num_inspirations=2
            )

        self.assertEqual(actual.parent.id, expected.parent.id)
        self.assertEqual(
            [item.id for item in actual.inspirations],
            [item.id for item in expected.inspirations],
        )
        self.assertEqual(resumed_ledger.spent(), ledger.spent())

    def test_boltzmann_runs_end_to_end_and_targets_each_island(self):
        config = make_config(selection=SelectionConfig(policy="boltzmann", seed=123))
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, client = make_controller(tmp, config=config)
            best = asyncio.run(controller.run(iterations=6))

        children = [
            program
            for program in controller.db._db.programs.values()
            if program.parent_id is not None
        ]
        self.assertEqual(len(client.calls), 6)
        self.assertIsNotNone(best)
        self.assertEqual({child.metadata["island"] for child in children}, {0, 1})
        self.assertTrue(
            all(child.metadata.get("sample_weight", 0.0) >= 0.05 for child in children)
        )

    def test_sampling_request_precedes_selection_advice(self):
        events = []

        class RecordingCoordination(NullCoordination):
            def sampling_request(self, ctx):
                events.append("request")
                return SamplingRequest({"future_hint": "value"})

            async def advise(self, ctx):
                events.append("advise")
                return Advice()

        class FakeEvaluator:
            async def evaluate_program(self, code, program_id):
                return {"combined_score": 0.5}

            def get_pending_artifacts(self, program_id):
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp)
            controller.coordination = RecordingCoordination()
            controller.evaluator = FakeEvaluator()
            controller.db.add(
                Program(
                    id="initial",
                    code=INITIAL_PROGRAM,
                    language="python",
                    metrics={"combined_score": 0.1},
                ),
                iteration=0,
                target_scope=0,
            )
            asyncio.run(controller._run_iteration(0))

        self.assertEqual(events[:2], ["request", "advise"])
        self.assertEqual(
            controller.substrate.log_snapshot()["ignored"],
            {"future_hint": "value"},
        )

    def test_boltzmann_policy_state_survives_controller_checkpoint(self):
        config = make_config(
            selection=SelectionConfig(policy="boltzmann", seed=123),
            database=DatabaseConfig(
                in_memory=True,
                num_islands=1,
                population_size=50,
                random_seed=42,
                migration_interval=1000,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp, config=config)
            for index in range(8):
                controller.db.add(
                    Program(
                        id=f"p{index}",
                        code=f"def f():\n    return {index}\n",
                        language="python",
                        metrics={"combined_score": index / 10},
                        metadata={"sample_weight": index + 1},
                    ),
                    target_scope=0,
                )
            for _ in range(4):
                controller.substrate.select(target_scope=0, num_inspirations=2)
            checkpoint = controller.save_checkpoint(0)
            expected = [
                controller.substrate.select(target_scope=0, num_inspirations=2).parent.id
                for _ in range(8)
            ]

            resumed, _, _ = make_controller(tmp, config=config)
            resumed.load_checkpoint(checkpoint)
            actual = [
                resumed.substrate.select(target_scope=0, num_inspirations=2).parent.id
                for _ in range(8)
            ]

        self.assertEqual(actual, expected)

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


class SequenceRewriteClient:
    """Fake client emitting full-rewrite programs returning the given values
    (the eval script scores value/10, so fitness is directly controlled)"""

    def __init__(self, values, prompt_tokens=10, completion_tokens=5):
        self.calls = []
        values = list(values)

        async def create(**params):
            self.calls.append(params)
            v = values[min(len(self.calls) - 1, len(values) - 1)]
            content = f"```python\ndef f():\n    return {v}\n```"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestNonImprovementRetry(unittest.TestCase):
    """retry_on="non_improvement" (task 0062): a valid child scoring <= parent
    retries up to retry_cap; the best attempt is kept either way."""

    def _run(self, tmp, values, retry_cap=2, retry_on="non_improvement"):
        config = make_config(retry_enabled=True, retry_cap=retry_cap, retry_on=retry_on)
        controller, ledger, client = make_controller(
            tmp, config=config, client=SequenceRewriteClient(values)
        )
        asyncio.run(controller.run(iterations=1))
        children = [p for p in controller.db._db.programs.values() if p.parent_id]
        return client, children

    def test_worse_then_better_stops_at_two_calls_and_stores_better(self):
        with tempfile.TemporaryDirectory() as tmp:
            # parent "return 1" scores 0.1; 0.5 -> 0.05 (worse), 5 -> 0.5 (better)
            client, children = self._run(tmp, values=[0.5, 5])
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].metrics["combined_score"], 0.5)
            retry_prompt = client.calls[1]["messages"][-1]["content"]
            self.assertIn("did not beat its parent", retry_prompt)
            self.assertIn("# Retry After Failure", retry_prompt)

    def test_all_worse_respects_cap_and_keeps_best_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            # all worse than parent 0.1: cap 1 -> exactly 2 calls; best kept
            client, children = self._run(tmp, values=[0.3, 0.5], retry_cap=1)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].metrics["combined_score"], 0.05)

    def test_trailing_failure_still_stores_best_valid_attempt(self):
        # attempt 0: valid but worse (recorded as best); attempt 1: garbage.
        # The stored child must be the valid attempt, reported eval_failed=False
        # (0062 verifier finding 4: no stale-failure state may leak through).
        contents = ["```python\ndef f():\n    return 0.5\n```", "no code here"]
        client = SimpleNamespace(calls=[])

        async def create(**params):
            client.calls.append(params)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content=contents[min(len(client.calls) - 1, 1)]))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(retry_enabled=True, retry_cap=1, retry_on="non_improvement")
            controller, _, _ = make_controller(tmp, config=config, client=client)
            asyncio.run(controller.run(iterations=1))
            children = [p for p in controller.db._db.programs.values() if p.parent_id]
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].metrics["combined_score"], 0.05)

    def test_retry_on_failure_default_ignores_non_improvement(self):
        with tempfile.TemporaryDirectory() as tmp:
            # regression pin: same worse child, default trigger -> no retry,
            # single call, child stored, no outcome text in any prompt
            client, children = self._run(tmp, values=[0.5], retry_on="failure")
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].metrics["combined_score"], 0.05)
            for call in client.calls:
                self.assertNotIn("did not beat its parent", call["messages"][-1]["content"])


class TestRetryLoop(unittest.TestCase):
    def test_overlength_response_is_rejected_without_crashing_run(self):
        config = make_config(
            max_code_length=10,
            retry_enabled=False,
            max_iterations=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, client = make_controller(tmp, config=config)
            asyncio.run(controller.run(iterations=1))

        self.assertEqual(len(client.calls), 1)
        self.assertGreater(ledger.spent("mutation"), 0)
        self.assertEqual(controller.db.num_programs, 1)
        self.assertEqual(controller.start_iteration, 1)

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


def make_pes_plan_client(plan_text):
    """Fake AsyncOpenAI for the PES coordination LLM (planning calls only)."""
    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=plan_text))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls)


def make_pes_directive_module(plan_text="# Plan\n## Strategy\n- try something\n## Action\n1. go"):
    ledger = TokenLedger(total_budget_tokens=1_000_000)
    coordination_llm = BudgetedLLM(
        model="fake-model", ledger=ledger, account="coordination", tag="pes.coordination",
        client=make_pes_plan_client(plan_text), retries=0, retry_delay=0.0,
    )
    return PESPlannerModule(
        config={"executor_mode": "directive"}, llm=coordination_llm, rng=random.Random(0)
    )


class FullRewriteFailThenSucceedClient:
    """Fake mutation client: first `fail_count` calls return garbage; then a
    valid full-rewrite ```python``` block."""

    def __init__(self, fail_count=1, prompt_tokens=100, completion_tokens=40):
        self.calls = []
        self._counter = 0

        async def create(**params):
            self.calls.append(params)
            self._counter += 1
            if self._counter <= fail_count:
                content = ""  # parse_full_rewrite falls back to plain-text
                # otherwise, so an empty response is the only reliable "no
                # parseable code" case in full-rewrite mode
            else:
                content = f"```python\ndef f():\n    return {self._counter + 1}\n```"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestPESDirectiveExecutor(unittest.TestCase):
    """Task 0065: executor_mode="directive" — the plan as the mutation call's
    primary instruction (verbatim LoongFlow executor prompt), dispatched via
    the `full_executor_prompt` attribution flag (Decision #25 scoped exemption)."""

    def _run(self, tmp, module, mutation_client, config=None):
        eval_path = os.path.join(tmp, "evaluator.py")
        with open(eval_path, "w") as f:
            f.write(EVAL_SCRIPT)
        config = config or make_config()
        ledger = TokenLedger(total_budget_tokens=1_000_000)
        mutation_llm = BudgetedLLM(
            model="fake-model", ledger=ledger, account="mutation", tag="mutate",
            client=mutation_client, retries=0, retry_delay=0.0,
        )
        controller = NoemaController(
            config=config,
            evaluation_file=eval_path,
            initial_program_code=INITIAL_PROGRAM,
            output_dir=os.path.join(tmp, "output"),
            mutation_llm=mutation_llm,
            coordination=module,
            ledger=ledger,
        )
        return controller

    def test_directive_end_to_end_produces_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = make_pes_directive_module()
            client = CyclingFakeClient()
            controller = self._run(tmp, module, client, config=make_config(max_iterations=3))
            asyncio.run(controller.run())

            self.assertGreater(controller.db.num_programs, 1)
            first_call = client.calls[0]
            self.assertTrue(
                first_call["messages"][0]["content"].startswith("You are an expert software developer")
            )
            self.assertIn("# Task Information", first_call["messages"][-1]["content"])
            self.assertIn("# Parent Solution", first_call["messages"][-1]["content"])

    def test_directive_retry_reformats_full_template_with_previous_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = make_pes_directive_module()
            client = FullRewriteFailThenSucceedClient(fail_count=1)
            config = make_config(retry_enabled=True, retry_cap=2)
            controller = self._run(tmp, module, client, config=config)
            asyncio.run(controller.run(iterations=1))

            self.assertEqual(len(client.calls), 2)
            first_user = client.calls[0]["messages"][-1]["content"]
            retry_user = client.calls[1]["messages"][-1]["content"]
            self.assertNotIn("Round 1, Candidate 0", first_user)
            self.assertIn("# Task Information", retry_user)  # full template re-formatted
            self.assertIn(
                "Round 1, Candidate 0, Evaluation Result: "
                "no parseable code block found in the response",
                retry_user,
            )

    def test_advisory_mode_through_controller_is_byte_identical(self):
        # Regression pin: executor_mode="advisory" (default) still produces
        # the standard mutation prompt with the plan as a suffix — same as
        # before task 0065 existed.
        with tempfile.TemporaryDirectory() as tmp:
            module = make_pes_directive_module()
            module.executor_mode = "advisory"
            client = CyclingFakeClient()
            controller = self._run(tmp, module, client)
            asyncio.run(controller.run(iterations=1))

            user_text = client.calls[0]["messages"][-1]["content"]
            self.assertIn(COORDINATION_HEADER, user_text)
            self.assertNotIn("# Task Information", user_text)
            self.assertNotIn("# Parent Solution", user_text)


class CyclingDiffFakeClient:
    """Fake AsyncOpenAI yielding valid SEARCH/REPLACE diffs against
    INITIAL_PROGRAM's return statement (only valid while the parent being
    mutated is still that exact text — fine for the single/few-iteration
    tests below, which only ever mutate the initial program)."""

    def __init__(self, prompt_tokens=100, completion_tokens=40):
        self.calls = []
        self._counter = 0

        async def create(**params):
            self.calls.append(params)
            self._counter += 1
            content = (
                "<<<<<<< SEARCH\n"
                "    return 1\n"
                "=======\n"
                f"    return {self._counter + 1}\n"
                ">>>>>>> REPLACE\n"
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                ),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class TestMutationOperatorMenu(unittest.TestCase):
    """Task 0027: EoH-derived operator menu, opt-in via NoemaConfig.mutation_operators."""

    def test_legacy_path_rng_never_advances_when_operators_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp)  # mutation_operators=None (default)
            initial_state = controller.mutation_operator_rng.getstate()
            for _ in range(5):
                operator = controller._choose_operator()
                self.assertEqual(operator.name, "legacy")
            self.assertEqual(controller.mutation_operator_rng.getstate(), initial_state)

    def test_legacy_operator_matches_diff_based_evolution_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp, config=make_config(diff_based_evolution=True))
            operator = controller._choose_operator()
            self.assertEqual(operator.template_key, "diff_user")
            self.assertEqual(operator.parse_mode, "diff")

            controller2, _, _ = make_controller(
                tmp, config=make_config(diff_based_evolution=False)
            )
            operator2 = controller2._choose_operator()
            self.assertEqual(operator2.template_key, "full_rewrite_user")
            self.assertEqual(operator2.parse_mode, "full_rewrite")

    def test_seeded_operator_sequence_matches_choice_in_a_loop(self):
        # Confirmed correctness trap (task 0027): random.Random(seed).choice()
        # called N times != .choices(k=N) — reconstruct with a loop, not choices().
        menu = ["m1", "m2"]
        seed = 7
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(mutation_operators=menu, mutation_operator_seed=seed)
            controller, _, _ = make_controller(tmp, config=config)
            actual = [controller._choose_operator().name for _ in range(20)]

        expected_rng = random.Random(seed)
        expected = [expected_rng.choice(menu) for _ in range(20)]
        self.assertEqual(actual, expected)

    def test_checkpoint_round_trip_preserves_operator_sequence(self):
        menu = ["m1", "m2", "m3"]
        seed = 3
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(mutation_operators=menu, mutation_operator_seed=seed)
            controller, _, _ = make_controller(tmp, config=config)
            asyncio.run(controller._ensure_initial_program())
            first_half = [controller._choose_operator().name for _ in range(10)]
            checkpoint = controller.save_checkpoint(0)

            controller2, _, _ = make_controller(tmp, config=config)
            controller2.load_checkpoint(checkpoint)
            second_half = [controller2._choose_operator().name for _ in range(10)]

        actual = first_half + second_half
        expected_rng = random.Random(seed)
        expected = [expected_rng.choice(menu) for _ in range(20)]
        self.assertEqual(actual, expected)

    def test_provenance_matches_actual_template_used(self):
        # Guards against reintroducing the pre-0027 duplicate-derivation bug
        # (child.prompts keyed by a re-derived template_key instead of the
        # one actually used to build the prompt).
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(
                mutation_operators=["m1"],
                database=DatabaseConfig(
                    in_memory=True, num_islands=1, population_size=50,
                    random_seed=42, migration_interval=1000,
                ),
            )
            controller, _, _ = make_controller(tmp, config=config, client=CyclingDiffFakeClient())
            asyncio.run(controller.run(iterations=1))

            children = [p for p in controller.db._db.programs.values() if p.parent_id is not None]
            self.assertEqual(len(children), 1)
            child = children[0]
            self.assertEqual(child.metadata["operator"], "m1")
            self.assertIn("eoh_m1_user", child.prompts)

    def test_arity_two_empty_inspirations_falls_back_without_crashing(self):
        # Iteration 0: only the initial program exists, so inspirations is
        # guaranteed empty. e1 (arity 2) must not crash.
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(
                mutation_operators=["e1"],
                database=DatabaseConfig(
                    in_memory=True, num_islands=1, population_size=50,
                    random_seed=42, migration_interval=1000,
                ),
            )
            controller, _, client = make_controller(tmp, config=config, client=CyclingFakeClient())
            asyncio.run(controller.run(iterations=1))
            self.assertEqual(len(client.calls), 1)
            children = [p for p in controller.db._db.programs.values() if p.parent_id is not None]
            self.assertEqual(len(children), 1)

    def test_arity_two_with_inspirations_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(
                mutation_operators=["e1"],
                num_inspirations=2,
                database=DatabaseConfig(
                    in_memory=True, num_islands=1, population_size=50,
                    random_seed=42, migration_interval=1000,
                ),
            )
            controller, _, client = make_controller(tmp, config=config, client=CyclingFakeClient())
            asyncio.run(controller.run(iterations=4))

            children = [p for p in controller.db._db.programs.values() if p.parent_id is not None]
            self.assertTrue(children)
            # By the later iterations the island has grown enough that at
            # least one arity-2 draw found a real second parent.
            saw_second_parent = any(
                "eoh_e1_user" in c.prompts
                and "# Second Parent Program" in c.prompts["eoh_e1_user"]["user"]
                for c in children
                if c.prompts
            )
            self.assertTrue(saw_second_parent, "expected at least one child built with a real parent2")


class TestArmRegistryCapabilityTable(unittest.TestCase):
    """The two PES arms are two registry KEYS, not one key plus sub-options
    (task 0066). Paired runs must differ in exactly one config setting —
    coordination.module — so this table pins what each key can and cannot do.
    If a capability ever migrates between arms, this fails."""

    @staticmethod
    def _pes_llm(response_text="# Plan\n\n## Strategy\n- vectorize the loop"):
        async def create(**params):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        return BudgetedLLM(
            model="fake-model",
            ledger=TokenLedger(total_budget_tokens=100_000),
            account="coordination",
            tag="pes.coordination",
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
            retries=0,
            retry_delay=0.0,
        )

    @staticmethod
    def _ctx():
        return GenerationContext(
            iteration=0,
            generation=0,
            island=0,
            parent=ProgramView(
                id="p", code="def f():\n    return 1\n", fitness=0.5, metrics={}
            ),
            best_fitness_history=[0.1],
            avg_fitness_history=[0.1],
        )

    def _seed_sibling_lineage(self, module):
        module._plans["other"] = {
            "plan": "# Plan\n\n## Strategy\n- simulated annealing",
            "outcome": "improved",
            "parent_id": "elsewhere",
            "parent_fitness": 0.4,
            "child_fitness": 0.6,
        }

    def _seed_reflection(self, module):
        module._plans["p"] = {
            "plan": "p",
            "outcome": "failed",
            "parent_id": "root",
            "parent_fitness": 0.5,
            "child_fitness": 0.0,
            "reflection": "the loop overran the array bound",
        }

    def test_capability_table(self):
        custom = build_coordination_module("pes-custom", llm=self._pes_llm())
        faithful = build_coordination_module("pes-faithful", llm=self._pes_llm())

        # Arm-defining knobs come from the KEY, not from params.
        self.assertEqual(custom.prompt_variant, "custom")
        self.assertEqual(custom.executor_mode, "advisory")
        self.assertEqual(faithful.prompt_variant, "faithful")
        self.assertEqual(faithful.executor_mode, "directive")

        # recent_block ("Recently Attempted Elsewhere"): custom only (#27).
        for module in (custom, faithful):
            self._seed_sibling_lineage(module)
        self.assertIn(
            "Recently Attempted Elsewhere", custom._planner._recent_strategies_block()
        )
        self.assertEqual(faithful._planner._recent_strategies_block(), "")
        self.assertEqual(faithful.recent_strategies_k, 0)

        # Reflection-seeded retry_advice: custom yields text, faithful "".
        ctx = self._ctx()
        for module in (custom, faithful):
            self._seed_reflection(module)
        self.assertIn(
            "overran the array bound",
            asyncio.run(custom.retry_advice(ctx, "IndexError", 1)),
        )
        self.assertEqual(asyncio.run(faithful.retry_advice(ctx, "IndexError", 1)), "")

        # Only faithful (directive) claims the whole mutation prompt.
        custom_advice = asyncio.run(custom.advise(ctx))
        faithful_advice = asyncio.run(faithful.advise(ctx))
        self.assertNotIn("full_executor_prompt", custom_advice.attribution)
        self.assertIs(faithful_advice.attribution["full_executor_prompt"], True)

    def test_pes_alias_resolves_to_custom_and_warns(self):
        with self.assertLogs("noema.coordination", level="WARNING") as logs:
            module = build_coordination_module("pes", llm=self._pes_llm())
        self.assertTrue(any("deprecated" in m for m in logs.output))
        self.assertIsInstance(module, PESCustomModule)
        self.assertEqual(module.prompt_variant, "custom")
        self.assertEqual(module.executor_mode, "advisory")

    def test_arm_defining_knobs_cannot_be_overridden_by_config(self):
        # Silent drift is the failure mode: a "pes-faithful" run that quietly
        # used custom prompts would still report itself as pes-faithful.
        for knob, value in (
            ("prompt_variant", "custom"),
            ("executor_mode", "advisory"),
            ("recent_strategies_k", 5),
        ):
            with self.assertRaises(ValueError) as cm:
                build_coordination_module("pes-faithful", params={knob: value})
            self.assertIn(knob, str(cm.exception))
        # Non-arm-defining params still pass through untouched.
        module = build_coordination_module("pes-faithful", params={"max_code_chars": 123})
        self.assertEqual(module.max_code_chars, 123)

    def test_registry_keys_and_no_pes_full(self):
        self.assertEqual(
            sorted(MODULE_REGISTRY),
            ["bandit", "hifo", "null", "pes-custom", "pes-faithful"],
        )
        # Decision #26: "pes-full" is a prose alias only, never a config key.
        self.assertNotIn("pes-full", MODULE_REGISTRY)
        self.assertNotIn("pes-full", DEPRECATED_ALIASES)


if __name__ == "__main__":
    unittest.main()
