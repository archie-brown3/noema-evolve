"""End-to-end: PE arm on the CVT substrate through the controller (task 0109).

Proves the host evaluates and inserts the module's proposed programs, that all
PE spend lands on the coordination ledger (mutation prompts untouched), and that
determinism/checkpoint hold.
"""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import BudgetConfig, CoordinationConfig, NoemaConfig, SubstrateConfig
from noema.controller import NoemaController
from noema.coordination.pe.module import PunctuatedEquilibriumModule

INITIAL = "def f():\n    return 1\n"

EVAL_SCRIPT = """\
import re

def evaluate(program_path):
    with open(program_path) as f:
        code = f.read()
    m = re.search(r"return (\\d+)", code)
    return {"combined_score": min(1.0, (float(m.group(1)) if m else 0.0) / 100.0)}
"""

# Behaviourally well-separated programs -> distinct CVT cells, so PE can cluster.
# Each maxes out a different behaviour axis (nesting+math / comprehension+range / trivial).
DIVERSE = [
    "def f():\n    t = 0\n    for i in range(20):\n        for j in range(20):\n"
    "            t = t + i * j - i + j * 2\n    return t\n",
    "def f():\n    return sum([x * x for x in range(400)])\n",
    "def f():\n    return 7\n",
]


def diverse_mutation_client():
    calls = []

    async def create(**params):
        calls.append(params)
        code = DIVERSE[(len(calls) - 1) % len(DIVERSE)]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=f"```python\n{code}\n```"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40),
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def paradigm_client():
    calls = []

    async def create(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=f"```python\ndef f():\n    return {50 + len(calls)}\n```"))],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=60),
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def build_pe_controller(tmp, ledger):
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as f:
        f.write(EVAL_SCRIPT)

    config = NoemaConfig(
        max_iterations=6,
        checkpoint_interval=100,
        diff_based_evolution=False,
        database=DatabaseConfig(in_memory=True, num_islands=2, population_size=50, random_seed=42),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        budget=BudgetConfig(total_tokens=10_000_000),
        substrate=SubstrateConfig(kind="cvt", cvt_n_centroids=64),
        coordination=CoordinationConfig(module="pe"),
    )
    mut_client, mut_calls = diverse_mutation_client()
    par_client, par_calls = paradigm_client()
    mutation_llm = BudgetedLLM(model="m", ledger=ledger, account="mutation", tag="mut",
                               client=mut_client, retries=0, retry_delay=0.0)
    coord_llm = BudgetedLLM(model="c", ledger=ledger, account="coordination", tag="pe",
                            client=par_client, retries=0, retry_delay=0.0)
    import random
    pe = PunctuatedEquilibriumModule(
        config={"interval": 2, "n_clusters": 2, "n_variants": 1,
                "domain_context": "Maximize.", "language": "python"},
        llm=coord_llm, rng=random.Random(0),
    )
    controller = NoemaController(
        config=config, evaluation_file=eval_path, initial_program_code=INITIAL,
        output_dir=os.path.join(tmp, "output"), mutation_llm=mutation_llm,
        coordination=pe, ledger=ledger,
    )
    return controller, par_calls


class TestPEOnCVTEndToEnd(unittest.TestCase):
    def test_host_evaluates_and_inserts_pe_proposals_billed_to_coordination(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TokenLedger(total_budget_tokens=10_000_000)
            controller, par_calls = build_pe_controller(tmp, ledger)
            asyncio.run(controller.run())

            # PE fired and the coordination LLM was actually called.
            self.assertGreaterEqual(controller.coordination.state_dict()["trigger_count"], 1)
            self.assertGreater(len(par_calls), 0)

            # The host inserted PE-authored programs into the store.
            inserted = [
                p for p in controller.db.population()
                if p.metadata.get("coordination_proposed")
            ]
            self.assertGreater(len(inserted), 0)
            origins = {p.metadata.get("origin") for p in inserted}
            self.assertTrue(origins & {"paradigm_shift", "variant"})

            # Metering: PE spend is on the coordination account; mutations separate.
            self.assertGreater(ledger.spent("coordination"), 0)
            self.assertGreater(ledger.spent("mutation"), 0)

    def test_checkpoint_resume_with_pe(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TokenLedger(total_budget_tokens=10_000_000)
            controller, _ = build_pe_controller(tmp, ledger)
            asyncio.run(controller.run(iterations=4))
            n = controller.db.num_programs
            ckpt_root = os.path.join(tmp, "output", "checkpoints")
            latest = os.path.join(ckpt_root, sorted(os.listdir(ckpt_root))[-1])

            ledger2 = TokenLedger(total_budget_tokens=10_000_000)
            controller2, _ = build_pe_controller(tmp, ledger2)
            controller2.load_checkpoint(latest)
            self.assertEqual(controller2.db.num_programs, n)


if __name__ == "__main__":
    unittest.main()
