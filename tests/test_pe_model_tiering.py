"""PE heavy/light model tiering tests (task 0110)."""

import asyncio
import os
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import CoordinationConfig, LLMClientConfig, LLMRolesConfig, NoemaConfig, SubstrateConfig
from noema.controller import NoemaController
from noema.coordination.pe.module import PunctuatedEquilibriumModule

from tests.test_pe_module import CODE_BLOCK, ELITES, ctx
from tests.test_pe_cvt_controller import EVAL_SCRIPT, INITIAL, diverse_mutation_client


class TaggingLLM:
    """Records (tag, model) per call; returns a distinct code block per call."""

    def __init__(self, model):
        self.model = model
        self.calls = []

    async def generate(self, prompt, **kw):
        self.calls.append((kw.get("tag"), self.model))
        return CODE_BLOCK % len(self.calls)


def make_pe(llm, **cfg):
    cfg.setdefault("interval", 10)
    cfg.setdefault("n_clusters", 3)
    cfg.setdefault("n_variants", 2)
    return PunctuatedEquilibriumModule(config=cfg, llm=llm, rng=random.Random(0))


class TestPEModelTiering(unittest.TestCase):
    def test_unconfigured_pe_uses_the_same_handle_for_both_tiers(self):
        llm = TaggingLLM("shared-model")
        pe = make_pe(llm)
        self.assertIs(pe._paradigm_llm, llm)
        self.assertIs(pe._variant_llm, llm)
        interv = asyncio.run(pe.on_generation_end(ctx(10)))
        models_used = {model for _, model in llm.calls}
        self.assertEqual(models_used, {"shared-model"})  # PR #61 behaviour unchanged

    def test_set_paradigm_llm_routes_paradigm_calls_only(self):
        base = TaggingLLM("light")
        heavy = TaggingLLM("heavy")
        pe = make_pe(base)
        pe.set_paradigm_llm(heavy)
        asyncio.run(pe.on_generation_end(ctx(10)))

        paradigm_calls = [c for c in heavy.calls if c[0] == "pe.paradigm_shift"]
        variant_calls_on_base = [c for c in base.calls if c[0] == "pe.variant"]
        self.assertEqual(len(paradigm_calls), 1)
        self.assertGreater(len(variant_calls_on_base), 0)
        self.assertEqual([c for c in base.calls if c[0] == "pe.paradigm_shift"], [])
        self.assertEqual([c for c in heavy.calls if c[0] == "pe.variant"], [])

    def test_set_variant_llm_routes_variant_calls_only(self):
        base = TaggingLLM("heavy")
        light = TaggingLLM("light")
        pe = make_pe(base)
        pe.set_variant_llm(light)
        asyncio.run(pe.on_generation_end(ctx(10)))

        self.assertEqual([c for c in base.calls if c[0] == "pe.variant"], [])
        self.assertTrue(all(c[0] == "pe.variant" for c in light.calls))
        self.assertGreater(len(light.calls), 0)

    def test_both_tiers_set_independently(self):
        base = TaggingLLM("base-unused")
        heavy = TaggingLLM("heavy")
        light = TaggingLLM("light")
        pe = make_pe(base)
        pe.set_paradigm_llm(heavy)
        pe.set_variant_llm(light)
        asyncio.run(pe.on_generation_end(ctx(10)))

        self.assertEqual(base.calls, [])  # base handle never used once both tiers set
        self.assertTrue(all(m == "heavy" for _, m in heavy.calls))
        self.assertTrue(all(m == "light" for _, m in light.calls))

    def test_missing_paradigm_llm_prevents_trigger(self):
        pe = make_pe(TaggingLLM("x"))
        pe._paradigm_llm = None
        self.assertIsNone(asyncio.run(pe.on_generation_end(ctx(10))))


def _fake_openai_response(model_tag: str, n: int):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=f"```python\ndef f():\n    return {n}\n```"))],
        usage=SimpleNamespace(prompt_tokens=150, completion_tokens=50),
    )


class TestPEModelTieringControllerEndToEnd(unittest.TestCase):
    """Full NoemaController build path: config -> controller._wire_alternate_tier
    -> a real second BudgetedLLM, with ledger accounting asserted."""

    def test_config_driven_tiering_routes_calls_and_charges_ledger_by_model(self):
        calls = []

        async def fake_create(**params):
            calls.append(params)
            return _fake_openai_response(params["model"], len(calls))

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )

        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)

            config = NoemaConfig(
                max_iterations=6,
                checkpoint_interval=100,
                diff_based_evolution=False,
                database=DatabaseConfig(in_memory=True, num_islands=2, population_size=50, random_seed=42),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
                substrate=SubstrateConfig(kind="cvt", cvt_n_centroids=64),
                llm=LLMRolesConfig(
                    mutation=LLMClientConfig(model="mut-model"),
                    coordination=LLMClientConfig(model="light-model"),
                ),
                coordination=CoordinationConfig(
                    module="pe",
                    params={
                        "interval": 2, "n_clusters": 2, "n_variants": 1,
                        "paradigm_model": "heavy-model",  # distinct from light-model
                    },
                ),
            )
            ledger = TokenLedger(total_budget_tokens=10_000_000)
            mut_client, _ = diverse_mutation_client()
            mutation_llm = BudgetedLLM(model="mut-model", ledger=ledger, account="mutation",
                                       tag="mut", client=mut_client, retries=0, retry_delay=0.0)

            with mock.patch("openai.AsyncOpenAI", return_value=fake_client):
                controller = NoemaController(
                    config=config, evaluation_file=eval_path, initial_program_code=INITIAL,
                    output_dir=os.path.join(tmp, "output"), mutation_llm=mutation_llm,
                    ledger=ledger,
                )
                # Tiering was wired without a fake `coordination=` bypassing the build path.
                self.assertIsInstance(controller.coordination, PunctuatedEquilibriumModule)
                self.assertIsNot(controller.coordination._paradigm_llm, controller.coordination._variant_llm)
                asyncio.run(controller.run())

            heavy_records = [r for r in ledger.records if r.model == "heavy-model"]
            light_records = [r for r in ledger.records if r.model == "light-model"]
            self.assertGreater(len(heavy_records), 0, "paradigm calls must bill heavy-model")
            self.assertGreater(len(light_records), 0, "variant calls must bill light-model")
            self.assertTrue(all(r.account == "coordination" for r in heavy_records + light_records))

    def test_unconfigured_pe_never_builds_a_second_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = os.path.join(tmp, "evaluator.py")
            with open(eval_path, "w") as f:
                f.write(EVAL_SCRIPT)
            config = NoemaConfig(
                max_iterations=2, checkpoint_interval=100, diff_based_evolution=False,
                database=DatabaseConfig(in_memory=True, num_islands=2, population_size=50, random_seed=42),
                evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
                substrate=SubstrateConfig(kind="cvt", cvt_n_centroids=64),
                coordination=CoordinationConfig(module="pe", params={"interval": 2}),
            )
            ledger = TokenLedger(total_budget_tokens=10_000_000)
            mut_client, _ = diverse_mutation_client()
            mutation_llm = BudgetedLLM(model="mut-model", ledger=ledger, account="mutation",
                                       tag="mut", client=mut_client, retries=0, retry_delay=0.0)
            with mock.patch("openai.AsyncOpenAI") as ctor:
                controller = NoemaController(
                    config=config, evaluation_file=eval_path, initial_program_code=INITIAL,
                    output_dir=os.path.join(tmp, "output"), mutation_llm=mutation_llm,
                    ledger=ledger,
                )
                self.assertIs(controller.coordination._paradigm_llm, controller.coordination._variant_llm)
                # Exactly one real client constructed (the default coordination
                # handle) — no alt-tier client when nothing was configured.
                self.assertEqual(ctor.call_count, 1)


if __name__ == "__main__":
    unittest.main()
