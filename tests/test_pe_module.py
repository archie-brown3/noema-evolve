"""Punctuated Equilibrium coordination module tests (task 0109)."""

import asyncio
import inspect
import random
import unittest

from openevolve.database import Program

import noema.coordination.pe.module as pe_module
from noema.coordination import MODULE_REGISTRY, build_coordination_module
from noema.coordination.base import GenerationContext, Intervention, PopulationSnapshot
from noema.views import ProgramView

CODE_BLOCK = "```python\ndef f():\n    return %d\n```"


class FakeLLM:
    """Records tags; returns a full-rewrite code block, billed nowhere (test)."""

    def __init__(self):
        self.calls = []

    async def generate(self, prompt, **kw):
        self.calls.append(kw.get("tag"))
        return CODE_BLOCK % len(self.calls)


def pv(pid, code, score):
    return ProgramView.from_program(
        Program(id=pid, code=code, metrics={"combined_score": score}), []
    )


ELITES = [
    pv("e1", "def f():\n    return 1\n", 0.3),
    pv("e2", "def f():\n    return sum(i for i in range(10))\n", 0.5),
    pv("e3", "def f():\n    t=0\n    for i in range(50):\n        t+=i\n    return t\n", 0.7),
    pv("e4", "def f():\n    return [x*2 for x in range(20)][0]\n", 0.4),
]


def ctx(iteration, elites=ELITES):
    snap = PopulationSnapshot(
        scope=None,
        top_programs=tuple(elites),
        fitnesses=tuple(e.fitness for e in elites),
        best_program=elites[-1] if elites else None,
        topology="cvt_regions",
    )
    return GenerationContext(
        iteration=iteration, generation=iteration,
        global_population=snap, local_population=snap,
    )


def make_pe(llm=None, seed=0, **cfg):
    cfg.setdefault("interval", 10)
    cfg.setdefault("n_clusters", 3)
    cfg.setdefault("n_variants", 2)
    cfg.setdefault("domain_context", "Maximize the return value.")
    return build_coordination_module("pe", cfg, llm=llm, rng=random.Random(seed))


class TestPunctuatedEquilibrium(unittest.TestCase):
    def test_registered(self):
        self.assertIn("pe", MODULE_REGISTRY)

    def test_advise_is_a_no_op_preserving_mutation_prompt(self):
        adv = asyncio.run(make_pe(FakeLLM()).advise(ctx(10)))
        self.assertEqual(adv.prompt_block, "")
        self.assertEqual(adv.system_block, "")
        self.assertEqual(adv.attribution, {})

    def test_fires_on_interval_with_paradigm_and_variants(self):
        pe = make_pe(FakeLLM(), n_variants=2)
        interv = asyncio.run(pe.on_generation_end(ctx(10)))
        self.assertIsInstance(interv, Intervention)
        origins = [p.origin for p in interv.proposals]
        self.assertIn("paradigm_shift", origins)
        self.assertEqual(origins.count("variant"), 2)
        for p in interv.proposals:
            self.assertTrue(p.code.strip())  # extracted real code

    def test_does_not_fire_off_interval(self):
        self.assertIsNone(asyncio.run(make_pe(FakeLLM()).on_generation_end(ctx(5))))

    def test_does_not_fire_at_iteration_zero(self):
        self.assertIsNone(asyncio.run(make_pe(FakeLLM()).on_generation_end(ctx(0))))

    def test_does_not_fire_without_llm(self):
        self.assertIsNone(asyncio.run(make_pe(None).on_generation_end(ctx(10))))

    def test_does_not_fire_with_too_few_elites(self):
        pe = make_pe(FakeLLM(), n_clusters=3)
        self.assertIsNone(asyncio.run(pe.on_generation_end(ctx(10, ELITES[:2]))))

    def test_clustering_is_deterministic_under_seed(self):
        a = asyncio.run(make_pe(FakeLLM(), seed=1, n_variants=0).on_generation_end(ctx(10)))
        b = asyncio.run(make_pe(FakeLLM(), seed=1, n_variants=0).on_generation_end(ctx(10)))
        self.assertEqual(a.proposals[0].parent_id, b.proposals[0].parent_id)

    def test_state_dict_round_trips(self):
        pe = make_pe(FakeLLM())
        asyncio.run(pe.on_generation_end(ctx(10)))
        self.assertEqual(pe.state_dict()["trigger_count"], 1)
        restored = make_pe(FakeLLM())
        restored.load_state_dict(pe.state_dict())
        self.assertEqual(restored.state_dict(), pe.state_dict())

    def test_paradigm_and_variant_calls_are_tagged(self):
        llm = FakeLLM()
        asyncio.run(make_pe(llm, n_variants=1).on_generation_end(ctx(10)))
        self.assertIn("pe.paradigm_shift", llm.calls)
        self.assertIn("pe.variant", llm.calls)

    def test_module_never_touches_store_or_evaluator_directly(self):
        # Structural proof of spec §3's metering boundary: PE PROPOSES via
        # Intervention; only the host evaluates (self.evaluator) and inserts
        # (db.add/store.add). The module source must reference neither.
        source = inspect.getsource(pe_module)
        for forbidden in ("db.add", "store.add", ".evaluate_program(", "self.evaluator",
                          "from noema.cvt import", "CVTStore"):
            self.assertNotIn(forbidden, source, f"PE module must not reference {forbidden!r}")

    def test_intervention_carries_proposals_not_side_effects(self):
        # The return value IS the effect boundary: on_generation_end must not
        # mutate anything outside itself — it only returns data.
        pe = make_pe(FakeLLM(), n_variants=1)
        before = pe.state_dict()
        result = asyncio.run(pe.on_generation_end(ctx(10)))
        self.assertIsInstance(result, Intervention)
        # State changed only through the documented state_dict (trigger_count),
        # nothing PE doesn't own.
        after = pe.state_dict()
        self.assertEqual(set(before) | {"trigger_count"}, set(after))


if __name__ == "__main__":
    unittest.main()
