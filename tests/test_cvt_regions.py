"""CVT region grouping tests (task 0111).

Unit-level grouping correctness on CVTStore directly, plus the portability
claim this ticket exists to satisfy: hifo and pes-faithful — both written
against a population-as-distribution local cohort — run end-to-end on a real
CVTStore with non-degenerate (>1 member) regions.
"""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from openevolve.config import DatabaseConfig, EvaluatorConfig
from openevolve.database import Program

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import CoordinationConfig, NoemaConfig, SubstrateConfig
from noema.controller import NoemaController
from noema.cvt import CVTStore
from noema.coordination.pe.module import PunctuatedEquilibriumModule

from tests.test_pe_cvt_controller import EVAL_SCRIPT, INITIAL, diverse_mutation_client


def prog(pid, code, score):
    return Program(id=pid, code=code, metrics={"combined_score": score})


# Behaviourally well-separated (mirrors test_pe_cvt_controller's DIVERSE set).
DIVERSE = [
    "def f():\n    t = 0\n    for i in range(20):\n        for j in range(20):\n"
    "            t = t + i * j - i + j * 2\n    return t\n",
    "def f():\n    return sum([x * x for x in range(400)])\n",
    "def f():\n    return 7\n",
    "def f():\n    return [x for x in range(300) if x % 2][0]\n",
]


def make_store(**kw):
    kw.setdefault("n_centroids", 64)
    kw.setdefault("seed", 7)
    kw.setdefault("feature_dimensions", ["x"])
    return CVTStore(**kw)


class TestRegionGrouping(unittest.TestCase):
    def test_default_ungrouped_is_unchanged(self):
        s = make_store()
        self.assertIsNone(s.num_regions)
        self.assertIsNone(s.target_scope(3))
        s.add(prog("p1", DIVERSE[0], 0.5))
        regions = s.regions()
        self.assertTrue(all(r.label.startswith("cell:") for r in regions))

    def test_grouped_store_covers_every_region_including_empty(self):
        s = make_store(num_regions=5)
        regions = s.regions()
        self.assertEqual(len(regions), 5)
        self.assertEqual({r.scope for r in regions}, set(range(5)))
        self.assertTrue(all(r.size == 0 for r in regions))  # nothing added yet

    def test_region_grouping_is_a_deterministic_pure_function(self):
        a = make_store(num_regions=4)._region_of_cell
        b = make_store(num_regions=4)._region_of_cell
        self.assertEqual(a, b)

    def test_region_cohort_is_non_degenerate(self):
        s = make_store(num_regions=3)
        for i, code in enumerate(DIVERSE):
            s.add(prog(f"p{i}", code, 0.1 * i))
        multi_member = [r for r in s.regions() if r.size > 1]
        self.assertTrue(multi_member, "expected at least one region with >1 program")
        big = multi_member[0]
        cohort = s.snapshot(scope=big.scope)
        self.assertGreater(len(cohort.fitnesses), 1)

    def test_target_scope_rotates_through_regions(self):
        s = make_store(num_regions=3)
        self.assertEqual([s.target_scope(i) for i in range(6)], [0, 1, 2, 0, 1, 2])

    def test_elites_and_top_programs_are_region_aware(self):
        s = make_store(num_regions=2)
        for i, code in enumerate(DIVERSE):
            s.add(prog(f"p{i}", code, float(i)))
        for region in (0, 1):
            pop = s.population(region)
            top = s.top_programs(len(pop), region)
            self.assertEqual(set(p.id for p in top), set(p.id for p in pop))

    def test_num_regions_exceeding_n_centroids_rejected(self):
        with self.assertRaises(ValueError):
            make_store(n_centroids=4, num_regions=8)

    def test_checkpoint_round_trip_preserves_grouping(self):
        s = make_store(num_regions=3)
        for i, code in enumerate(DIVERSE):
            s.add(prog(f"p{i}", code, float(i)))
        with tempfile.TemporaryDirectory() as d:
            s.save(d, iteration=2)
            r = make_store(num_regions=3)
            r.load(d)
            self.assertEqual(r.num_regions, 3)
            self.assertEqual(r._region_of_cell, s._region_of_cell)
            self.assertEqual(r.regions(), s.regions())

    def test_pe_is_unaffected_by_region_grouping(self):
        # PE clusters elite CODE directly (task 0109), never reads regions() —
        # setting num_regions must not change its behaviour.
        s_ungrouped = make_store()
        s_grouped = make_store(num_regions=3)
        for i, code in enumerate(DIVERSE):
            s_ungrouped.add(prog(f"p{i}", code, float(i)))
            s_grouped.add(prog(f"p{i}", code, float(i)))
        self.assertEqual(
            [p.code for p in s_ungrouped.snapshot(None).top_programs],
            [p.code for p in s_grouped.snapshot(None).top_programs],
        )


def _fake_response(content: str, n: int):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=30),
    )


def _build_controller(tmp, module: str, num_regions: int):
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as f:
        f.write(EVAL_SCRIPT)
    config = NoemaConfig(
        max_iterations=6,
        checkpoint_interval=100,
        diff_based_evolution=False,
        database=DatabaseConfig(in_memory=True, num_islands=2, population_size=50, random_seed=42),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        substrate=SubstrateConfig(kind="cvt", cvt_n_centroids=64, cvt_num_regions=num_regions),
        coordination=CoordinationConfig(module=module),
    )
    ledger = TokenLedger(total_budget_tokens=10_000_000)
    mut_client, _ = diverse_mutation_client()
    mutation_llm = BudgetedLLM(model="mut-model", ledger=ledger, account="mutation",
                              tag="mut", client=mut_client, retries=0, retry_delay=0.0)
    return config, ledger, mutation_llm, eval_path


class TestCoordinationArmsOnGroupedCVT(unittest.TestCase):
    """The portability claim: hifo/pes-faithful run end-to-end on a real
    CVTStore with region grouping, and see a non-degenerate local cohort."""

    def test_hifo_runs_end_to_end_with_non_degenerate_local_cohort(self):
        calls = []

        async def fake_create(**params):
            calls.append(params)
            return _fake_response(
                "Design principle: prefer explicit loops over nested comprehensions "
                "for clarity and performance in tight numeric kernels.", len(calls)
            )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )
        with tempfile.TemporaryDirectory() as tmp:
            config, ledger, mutation_llm, eval_path = _build_controller(tmp, "hifo", num_regions=3)
            with mock.patch("openai.AsyncOpenAI", return_value=fake_client):
                controller = NoemaController(
                    config=config, evaluation_file=eval_path, initial_program_code=INITIAL,
                    output_dir=os.path.join(tmp, "output"), mutation_llm=mutation_llm,
                    ledger=ledger,
                )
                self.assertIsInstance(controller.db, CVTStore)
                self.assertEqual(controller.db.num_regions, 3)
                asyncio.run(controller.run())

            self.assertGreater(controller.db.num_programs, 1)
            # At least one region ended up with a genuine population (>1),
            # not a single-cell elite — the property this ticket exists for.
            multi_member = [r for r in controller.db.regions() if r.size > 1]
            self.assertTrue(multi_member, "expected a non-degenerate region cohort")

    def test_pes_faithful_runs_end_to_end_with_non_degenerate_local_cohort(self):
        calls = []

        async def fake_create(**params):
            calls.append(params)
            content = (
                "## Plan Outline 1\nUse a direct approach.\n"
                "### Final Child Solution Generation Plan\nRefine the current solution."
            )
            return _fake_response(content, len(calls))

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )
        with tempfile.TemporaryDirectory() as tmp:
            config, ledger, mutation_llm, eval_path = _build_controller(
                tmp, "pes-faithful", num_regions=3
            )
            with mock.patch("openai.AsyncOpenAI", return_value=fake_client):
                controller = NoemaController(
                    config=config, evaluation_file=eval_path, initial_program_code=INITIAL,
                    output_dir=os.path.join(tmp, "output"), mutation_llm=mutation_llm,
                    ledger=ledger,
                )
                asyncio.run(controller.run())

            self.assertGreater(controller.db.num_programs, 1)
            multi_member = [r for r in controller.db.regions() if r.size > 1]
            self.assertTrue(multi_member, "expected a non-degenerate region cohort")

    def test_pe_runs_end_to_end_on_a_grouped_store_too(self):
        # PE doesn't use regions(), but the substrate wiring must not break it
        # when num_regions happens to be configured for a different arm's sake.
        with tempfile.TemporaryDirectory() as tmp:
            config, ledger, mutation_llm, eval_path = _build_controller(tmp, "pe", num_regions=3)
            controller = NoemaController(
                config=config, evaluation_file=eval_path, initial_program_code=INITIAL,
                output_dir=os.path.join(tmp, "output"), mutation_llm=mutation_llm,
                ledger=ledger,
                coordination=PunctuatedEquilibriumModule(
                    config={"interval": 2, "n_clusters": 2, "n_variants": 1},
                    llm=BudgetedLLM(model="c", ledger=ledger, account="coordination", tag="pe",
                                    client=diverse_mutation_client()[0], retries=0, retry_delay=0.0),
                ),
            )
            asyncio.run(controller.run())
            self.assertGreater(controller.db.num_programs, 1)


if __name__ == "__main__":
    unittest.main()
