"""
Tests for the transplanted HiFo-Prompt mechanism (noema.coordination.hifo).

Unit tests check the borrowed InsightPool / EvolutionaryNavigator against
hand-computed traces; integration tests assert tips reach prompts, credit
reaches tip stats, and extraction charges the coordination ledger account.
"""

import asyncio
import json
import random
import unittest
from types import SimpleNamespace

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.coordination import build_coordination_module
from noema.coordination.base import GenerationContext
from noema.coordination.hifo.evolutionary_navigator import EvolutionaryNavigator
from noema.coordination.hifo.insight_pool import InsightPool
from noema.coordination.hifo.module import HiFoPromptModule, INSIGHTS_PREFIX
from noema.views import ProgramView


def make_view(fitness=0.5, code="def f():\n    return 1\n", desc="") -> ProgramView:
    return ProgramView(id="p", code=code, fitness=fitness, changes_description=desc)


def make_ctx(**overrides) -> GenerationContext:
    defaults = dict(iteration=0, generation=0, island=0, parent=make_view())
    defaults.update(overrides)
    return GenerationContext(**defaults)


class TestInsightPool(unittest.TestCase):
    def test_seeded_with_default_tips(self):
        pool = InsightPool(rng=random.Random(0))
        self.assertEqual(len(pool.tips), 5)

    def test_add_tip_rejects_near_duplicates(self):
        pool = InsightPool(initial_tips=["use dynamic programming for overlapping subproblems"])
        self.assertFalse(pool.add_tip("use dynamic programming for overlapping subproblems please"))
        self.assertTrue(pool.add_tip("prefer greedy choices with provable bounds"))
        self.assertEqual(len(pool.tips), 2)

    def test_update_tip_stats_ema_hand_computed(self):
        pool = InsightPool(initial_tips=["tip one alpha", "tip two beta"])
        # EMA with alpha=0.3 starting from 0.0: one update of 0.5 -> 0.15
        pool.update_tip_stats("tip one alpha", 0.5)
        self.assertAlmostEqual(pool.tip_stats["tip one alpha"]["effectiveness"], 0.15)
        # Second update of 0.5: 0.7*0.15 + 0.3*0.5 = 0.255
        pool.update_tip_stats("tip one alpha", 0.5)
        self.assertAlmostEqual(pool.tip_stats["tip one alpha"]["effectiveness"], 0.255)
        # Clamped to [-1, 1]
        for _ in range(50):
            pool.update_tip_stats("tip two beta", -5.0)
        self.assertGreaterEqual(pool.tip_stats["tip two beta"]["effectiveness"], -1.0)

    def test_probation_immunity_on_eviction(self):
        # Pool of 2: one mature bad tip, one fresh (probation) tip. The mature
        # one must be evicted even though the fresh tip has lower effectiveness
        pool = InsightPool(max_size=2, initial_tips=["mature bad tip xxx", "fresh tip yyy"])
        stats = pool.tip_stats["mature bad tip xxx"]
        stats["used_count"] = 10  # past probation (threshold 3)
        stats["effectiveness"] = -0.9
        # fresh tip stays at used_count=0 -> probation immunity

        pool.add_tip("brand new incoming tip zzz")
        self.assertIn("fresh tip yyy", pool.tips)
        self.assertNotIn("mature bad tip xxx", pool.tips)
        self.assertIn("brand new incoming tip zzz", pool.tips)

    def test_get_tips_marks_usage(self):
        pool = InsightPool(initial_tips=["tip one alpha", "tip two beta"], rng=random.Random(0))
        pool.update_generation(4)
        tips = pool.get_tips(k=3)  # pool smaller than k -> all tips returned
        self.assertEqual(len(tips), 2)
        for tip in tips:
            self.assertEqual(pool.tip_stats[tip]["used_count"], 1)
            self.assertEqual(pool.tip_stats[tip]["last_used_generation"], 4)

    def test_adaptive_strategy_prefers_effective_tips(self):
        tips = [f"distinct tip number {i} {'x' * i}" for i in range(6)]
        pool = InsightPool(initial_tips=tips, rng=random.Random(0))
        pool.update_tip_stats(tips[3], 1.0)  # make one tip clearly best
        selected = pool.get_tips(k=2, strategy="adaptive")
        self.assertIn(tips[3], selected)

    def test_state_dict_round_trip(self):
        pool = InsightPool(initial_tips=["tip one alpha", "tip two beta"])
        pool.update_generation(7)
        pool.update_tip_stats("tip one alpha", 0.5)
        state = json.loads(json.dumps(pool.state_dict()))  # must be JSON-safe

        restored = InsightPool(initial_tips=["other"])
        restored.load_state_dict(state)
        self.assertEqual(list(restored.tips), ["tip one alpha", "tip two beta"])
        self.assertEqual(restored.current_generation, 7)
        self.assertAlmostEqual(restored.tip_stats["tip one alpha"]["effectiveness"], 0.15)


class TestEvolutionaryNavigator(unittest.TestCase):
    def test_short_history_returns_balanced(self):
        nav = EvolutionaryNavigator(maximize=True, rng=random.Random(0))
        regime, directive = nav.get_guidance(best_fitness_history=[0.5])
        self.assertEqual(regime, "balanced")
        self.assertTrue(directive)

    def test_stagnation_triggers_exploration(self):
        nav = EvolutionaryNavigator(maximize=True, rng=random.Random(0))
        # Flat history: each call sees no improvement (maximize convention)
        history = [0.5, 0.5]
        regime = None
        for _ in range(4):  # first call sets last_best, next three stagnate
            regime, _ = nav.get_guidance(best_fitness_history=history, diversity_history=[0.9])
            history = history + [0.5]
        self.assertEqual(nav.stagnation_count, 3)
        self.assertEqual(regime, "exploration")

    def test_improvement_streak_triggers_exploitation_with_maximize(self):
        nav = EvolutionaryNavigator(maximize=True, rng=random.Random(0))
        # Strictly improving (rising) fitness — under the original's
        # minimization sign this would count as stagnation
        histories = [[0.1, 0.2], [0.1, 0.2, 0.3], [0.1, 0.2, 0.3, 0.4]]
        regime = None
        for history in histories:
            regime, _ = nav.get_guidance(best_fitness_history=history, diversity_history=[0.9])
        self.assertEqual(nav.improvement_count, 2)
        self.assertEqual(nav.stagnation_count, 0)
        self.assertEqual(regime, "exploitation")

    def test_low_diversity_triggers_exploration(self):
        nav = EvolutionaryNavigator(maximize=True, rng=random.Random(0))
        nav.get_guidance(best_fitness_history=[0.1, 0.2], diversity_history=[0.9])
        regime, _ = nav.get_guidance(best_fitness_history=[0.1, 0.2, 0.3], diversity_history=[0.1])
        self.assertEqual(regime, "exploration")

    def test_state_dict_round_trip(self):
        nav = EvolutionaryNavigator(maximize=True, rng=random.Random(0))
        for history in ([0.5, 0.5], [0.5, 0.5, 0.5]):
            nav.get_guidance(best_fitness_history=history, diversity_history=[0.9])
        state = json.loads(json.dumps(nav.state_dict()))

        restored = EvolutionaryNavigator()
        restored.load_state_dict(state)
        self.assertEqual(restored.stagnation_count, nav.stagnation_count)
        self.assertEqual(restored.last_best_fitness, 0.5)
        self.assertEqual(restored.last_guidance, nav.last_guidance)


class TestEffectiveness(unittest.TestCase):
    """Hand-computed traces for the maximization-adapted credit formula"""

    def setUp(self):
        self.module = HiFoPromptModule(rng=random.Random(0))

    def effectiveness(self, fitness, population, eval_failed=False, child_missing=False):
        child = None if child_missing else make_view(fitness=fitness)
        return self.module._calculate_insight_effectiveness(child, population, eval_failed)

    def test_failure_scores_minus_half(self):
        self.assertEqual(self.effectiveness(0.9, [0.1, 0.5], eval_failed=True), -0.5)
        self.assertEqual(self.effectiveness(0.0, [0.1, 0.5], child_missing=True), -0.5)

    def test_empty_population_scores_zero(self):
        self.assertEqual(self.effectiveness(0.9, []), 0.0)

    def test_degenerate_population_scores_tenth(self):
        self.assertEqual(self.effectiveness(0.9, [0.5, 0.5]), 0.1)

    def test_new_best_scores_high(self):
        # pop best=0.8, worst=0.2; child 0.9 beats best: norm=(0.9-0.2)/0.6=1.1667
        # effectiveness = 0.8 + 0.2*1.1667 = 1.0333 -> clamped to 1.0
        self.assertAlmostEqual(self.effectiveness(0.9, [0.2, 0.5, 0.8]), 1.0)

    def test_above_average_scores_moderate(self):
        # pop: best=0.8, worst=0.2, avg=0.5; child 0.6: norm=(0.6-0.2)/0.6=0.6667
        # effectiveness = 0.2 + 0.6*0.6667 = 0.6
        self.assertAlmostEqual(self.effectiveness(0.6, [0.2, 0.5, 0.8]), 0.6)

    def test_below_average_scores_low(self):
        # child 0.3: norm=(0.3-0.2)/0.6=0.1667; eff = -0.3 + 0.5*0.1667 = -0.2167
        self.assertAlmostEqual(self.effectiveness(0.3, [0.2, 0.5, 0.8]), -0.21666, places=4)


def make_extraction_client(response_text):
    """Fake AsyncOpenAI returning a fixed extraction response"""
    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=30),
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), calls=calls
    )
    return client


class TestHiFoPromptModule(unittest.TestCase):
    def make_module(self, extraction_response="- Nothing", **params):
        ledger = TokenLedger(total_budget_tokens=100_000)
        client = make_extraction_client(extraction_response)
        llm = BudgetedLLM(
            model="fake-model",
            ledger=ledger,
            account="coordination",
            tag="hifo.coordination",
            client=client,
            retries=0,
            retry_delay=0.0,
        )
        module = HiFoPromptModule(config=params, llm=llm, rng=random.Random(0))
        return module, ledger, client

    def test_advise_injects_tips_directive_and_regime(self):
        module, _, _ = self.make_module()
        advice = asyncio.run(module.advise(make_ctx(best_fitness_history=[0.1, 0.2])))

        self.assertIn(INSIGHTS_PREFIX, advice.prompt_block)
        self.assertIn("please pay special attention to:", advice.prompt_block)
        self.assertEqual(len(advice.attribution["insights"]), 3)
        self.assertIn(advice.attribution["regime"], ("exploration", "exploitation", "balanced"))
        # Every injected tip appears verbatim as a bullet
        for tip in advice.attribution["insights"]:
            self.assertIn(f"- {tip}", advice.prompt_block)

    def test_report_result_updates_used_tips_only(self):
        module, _, _ = self.make_module()
        ctx = make_ctx(island_fitnesses=[0.2, 0.5, 0.8])
        advice = asyncio.run(module.advise(ctx))
        used = advice.attribution["insights"]
        unused = [tip for tip in module.insight_pool.tips if tip not in used]

        child = make_view(fitness=0.9)  # new population best
        module.report_result(ctx, child, advice.attribution, eval_failed=False)

        for tip in used:
            self.assertAlmostEqual(module.insight_pool.tip_stats[tip]["effectiveness"], 0.3 * 1.0)
        for tip in unused:
            self.assertEqual(module.insight_pool.tip_stats[tip]["effectiveness"], 0.0)

    def test_failed_child_penalizes_tips(self):
        module, _, _ = self.make_module()
        ctx = make_ctx(island_fitnesses=[0.2, 0.8])
        advice = asyncio.run(module.advise(ctx))
        module.report_result(ctx, None, advice.attribution, eval_failed=True)
        for tip in advice.attribution["insights"]:
            self.assertAlmostEqual(module.insight_pool.tip_stats[tip]["effectiveness"], 0.3 * -0.5)

    def test_extraction_adds_tips_and_charges_coordination_account(self):
        response = (
            "Here are the principles:\n"
            "- Exploit sparse matrix structure to skip redundant computation entirely\n"
            "- tiny\n"  # under min length, must be dropped
            "- Cache intermediate scoring results across neighborhood evaluations\n"
        )
        module, ledger, client = self.make_module(
            extraction_response=response, extraction_probability=1.0
        )
        ctx = make_ctx(
            generation=2,
            top_programs=[make_view(fitness=0.9, desc="greedy with lookahead scoring")],
        )
        asyncio.run(module.on_generation_end(ctx))

        self.assertIn(
            "Exploit sparse matrix structure to skip redundant computation entirely",
            module.insight_pool.tips,
        )
        self.assertIn(
            "Cache intermediate scoring results across neighborhood evaluations",
            module.insight_pool.tips,
        )
        self.assertNotIn("tiny", module.insight_pool.tips)
        # The mechanism's LLM call drew from the coordination account
        self.assertEqual(ledger.spent("coordination"), 230)
        self.assertEqual(ledger.spent("mutation"), 0)
        # The extraction prompt used the program's description, HiFo-style
        prompt_text = client.calls[0]["messages"][-1]["content"]
        self.assertIn("greedy with lookahead scoring", prompt_text)

    def test_extraction_probability_zero_never_calls_llm(self):
        module, ledger, client = self.make_module(extraction_probability=0.0)
        ctx = make_ctx(generation=1, top_programs=[make_view()])
        asyncio.run(module.on_generation_end(ctx))
        self.assertEqual(len(client.calls), 0)
        self.assertEqual(ledger.spent(), 0)

    def test_state_dict_round_trip(self):
        module, _, _ = self.make_module()
        ctx = make_ctx(island_fitnesses=[0.2, 0.8], best_fitness_history=[0.1, 0.2])
        advice = asyncio.run(module.advise(ctx))
        module.report_result(ctx, make_view(fitness=0.9), advice.attribution, False)
        state = json.loads(json.dumps(module.state_dict()))

        module2, _, _ = self.make_module()
        module2.load_state_dict(state)
        self.assertEqual(list(module2.insight_pool.tips), list(module.insight_pool.tips))
        self.assertEqual(module2.navigator.last_best_fitness, module.navigator.last_best_fitness)

    def test_log_snapshot_json_serializable(self):
        module, _, _ = self.make_module()
        asyncio.run(module.advise(make_ctx(best_fitness_history=[0.1, 0.2])))
        json.dumps(module.log_snapshot())

    def test_registered_in_module_registry(self):
        module = build_coordination_module("hifo", params={"tips_per_prompt": 2})
        self.assertIsInstance(module, HiFoPromptModule)
        self.assertEqual(module.tips_per_prompt, 2)


class TestTwoArmPilot(unittest.TestCase):
    """
    Plan task B11: run the OFF arm and the HiFo arm through the controller with
    identical stubbed mutation responses and verify (a) the only difference in
    mutation prompts is the coordination suffix, and (b) the ledger splits
    spend by account.
    """

    EVAL_SCRIPT = (
        "import re\n"
        "def evaluate(program_path):\n"
        "    with open(program_path) as f:\n"
        "        code = f.read()\n"
        "    m = re.search(r'return (\\d+)', code)\n"
        "    value = float(m.group(1)) if m else 0.0\n"
        "    return {'combined_score': min(1.0, value / 10.0)}\n"
    )

    def _mutation_client(self):
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

    def _run_arm(self, tmp, arm, iterations=4):
        import os

        from openevolve.config import DatabaseConfig, EvaluatorConfig

        from noema.config import NoemaConfig
        from noema.controller import NoemaController
        from noema.coordination import NullCoordination

        eval_path = os.path.join(tmp, "evaluator.py")
        if not os.path.exists(eval_path):
            with open(eval_path, "w") as f:
                f.write(self.EVAL_SCRIPT)

        config = NoemaConfig(
            max_iterations=iterations,
            checkpoint_interval=100,
            diff_based_evolution=False,
            database=DatabaseConfig(
                in_memory=True,
                num_islands=2,
                population_size=50,
                random_seed=42,
                migration_interval=1000,
            ),
            evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        )

        ledger = TokenLedger(total_budget_tokens=1_000_000)
        mutation_client = self._mutation_client()
        mutation_llm = BudgetedLLM(
            model="fake-model",
            ledger=ledger,
            account="mutation",
            tag="mutate",
            client=mutation_client,
            retries=0,
            retry_delay=0.0,
        )

        if arm == "hifo":
            extraction_client = make_extraction_client(
                "- Prefer arithmetic identities over iteration when computing constants"
            )
            coordination_llm = BudgetedLLM(
                model="fake-model",
                ledger=ledger,
                account="coordination",
                tag="hifo.coordination",
                client=extraction_client,
                retries=0,
                retry_delay=0.0,
            )
            coordination = HiFoPromptModule(
                config={"extraction_probability": 1.0},
                llm=coordination_llm,
                rng=random.Random(43),
            )
        else:
            coordination = NullCoordination()

        controller = NoemaController(
            config=config,
            evaluation_file=eval_path,
            initial_program_code="def f():\n    return 1\n",
            output_dir=os.path.join(tmp, f"output_{arm}"),
            mutation_llm=mutation_llm,
            coordination=coordination,
            ledger=ledger,
        )
        asyncio.run(controller.run())
        return controller, ledger, mutation_client

    def test_prompts_identical_except_coordination_block(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_off, tempfile.TemporaryDirectory() as tmp_on:
            _, ledger_off, client_off = self._run_arm(tmp_off, "off")
            _, ledger_on, client_on = self._run_arm(tmp_on, "hifo")

            self.assertEqual(len(client_off.calls), len(client_on.calls))
            for call_off, call_on in zip(client_off.calls, client_on.calls):
                user_off = call_off["messages"][-1]["content"]
                user_on = call_on["messages"][-1]["content"]
                # Shared prefix is byte-identical; HiFo adds only a suffix block
                self.assertTrue(user_on.startswith(user_off))
                suffix = user_on[len(user_off) :]
                self.assertIn(INSIGHTS_PREFIX, suffix)

            # Ledger splits spend by account; both arms' mutation spend matches
            self.assertEqual(ledger_off.spent("coordination"), 0)
            self.assertGreater(ledger_on.spent("coordination"), 0)
            self.assertEqual(ledger_off.spent("mutation"), ledger_on.spent("mutation"))

    def test_hifo_arm_learns_extracted_tip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = self._run_arm(tmp, "hifo")
            self.assertIn(
                "Prefer arithmetic identities over iteration when computing constants",
                controller.coordination.insight_pool.tips,
            )
            # Generation log carries the HiFo snapshot for the run record
            self.assertTrue(controller.generation_log)
            snapshot = controller.generation_log[-1]["coordination"]
            self.assertIn("current_insight_count", snapshot)


if __name__ == "__main__":
    unittest.main()
