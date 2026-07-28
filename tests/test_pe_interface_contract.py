"""PR0122 regression: PE proposals must splice into the scaffold, never submit
raw LLM output that omits the fixed harness (run_bin_packing etc.)."""

import asyncio
import random
import unittest

from noema.coordination import build_coordination_module
from noema.coordination.base import GenerationContext, PopulationSnapshot
from noema.evolution.views import ProgramView
from openevolve.database import Program

# Minimal scaffold with EVOLVE-BLOCK markers — mirrors the real bin-packing harness
SCAFFOLD = """\
def run_bin_packing():
    return priority(1, [50])

# EVOLVE-BLOCK-START
def priority(item, bins):
    return -(bins - item)
# EVOLVE-BLOCK-END
"""


def pv(pid, code, score):
    return ProgramView.from_program(
        Program(id=pid, code=code, metrics={"combined_score": score}), []
    )


ELITES = [
    pv("e1", SCAFFOLD.replace("-(bins - item)", "1.0"), 0.3),
    pv("e2", SCAFFOLD.replace("-(bins - item)", "bins / item"), 0.5),
    pv("e3", SCAFFOLD, 0.7),
    pv("e4", SCAFFOLD.replace("-(bins - item)", "item * 0.1"), 0.4),
]


def ctx(iteration=10, elites=ELITES):
    snap = PopulationSnapshot(
        scope=None,
        top_programs=tuple(elites),
        fitnesses=tuple(e.fitness for e in elites),
        best_program=elites[2],
        topology="cvt_regions",
    )
    return GenerationContext(
        iteration=iteration, generation=iteration,
        global_population=snap, local_population=snap,
    )


class StandaloneLLM:
    """Simulates model returning only a priority function (no harness) — the observed failure."""
    async def generate(self, prompt, **kw):
        return "```python\ndef priority(item, bins):\n    return item / (bins + 1)\n```"


class FullProgramLLM:
    """Simulates model returning a full program that omits run_bin_packing."""
    async def generate(self, prompt, **kw):
        return "```python\nimport numpy as np\ndef priority(item, bins):\n    return np.exp(-bins/100)\n```"


def make_pe(llm, **cfg):
    cfg.setdefault("interval", 10)
    cfg.setdefault("n_clusters", 3)
    cfg.setdefault("n_variants", 1)
    cfg.setdefault("domain_context", "Pack bins.")
    return build_coordination_module("pe", cfg, llm=llm, rng=random.Random(0))


class TestPEInterfaceContract(unittest.TestCase):
    def _assert_proposals_have_harness(self, interv):
        self.assertIsNotNone(interv, "PE should produce proposals")
        self.assertGreater(len(interv.proposals), 0)
        for p in interv.proposals:
            self.assertIn(
                "run_bin_packing", p.code,
                f"Proposal missing run_bin_packing:\n{p.code[:300]}"
            )
            self.assertIn("# EVOLVE-BLOCK-START", p.code)
            self.assertIn("priority", p.code)

    def test_standalone_priority_response_splices_into_scaffold(self):
        """LLM returns only def priority — must be spliced into scaffold, not submitted raw."""
        pe = make_pe(StandaloneLLM())
        interv = asyncio.run(pe.on_generation_end(ctx()))
        self._assert_proposals_have_harness(interv)

    def test_full_program_without_harness_is_corrected(self):
        """LLM returns a full program missing run_bin_packing — must be corrected."""
        pe = make_pe(FullProgramLLM())
        interv = asyncio.run(pe.on_generation_end(ctx()))
        self._assert_proposals_have_harness(interv)


if __name__ == "__main__":
    unittest.main()
