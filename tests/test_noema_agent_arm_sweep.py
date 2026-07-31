"""Arm transfer under the agent host (side research, task 0160).

For every key in ``MODULE_REGISTRY``, drive ``AgentSession`` the way a compliant
agent would and check that the host still calls the coordination hooks. Arm
internals are covered elsewhere; these tests only answer: *does this arm still
participate when the mutation layer is an agent instead of a BudgetedLLM?*
"""

import asyncio
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.agenthost import AgentSession
from noema.budget.ledger import TokenLedger
from noema.budget.llm import BudgetedLLM
from noema.config import BudgetConfig, NoemaConfig
from noema.coordination import MODULE_REGISTRY, build_coordination_module
from noema.evolution.prompts import COORDINATION_HEADER

def _scaffold(body: str) -> str:
    """PE proposals splice into EVOLVE-BLOCK markers; plain functions are dropped."""
    return (
        "def run():\n    return f()\n\n"
        "# EVOLVE-BLOCK-START\n"
        f"{body}"
        "# EVOLVE-BLOCK-END\n"
    )


INITIAL_PROGRAM = _scaffold("def f():\n    return 1\n")

# Exec-based: PE seeds use loops/comprehensions, not bare `return N` literals.
EVAL_SCRIPT = """\
def evaluate(program_path):
    with open(program_path) as f:
        code = f.read()
    ns = {}
    try:
        exec(code, ns, ns)
        if "f" in ns:
            value = float(ns["f"]())
        elif "run" in ns:
            value = float(ns["run"]())
        else:
            return {"error": "program defines neither f nor run"}
    except Exception as exc:
        return {"error": repr(exc)}
    return {"combined_score": min(1.0, value / 10.0)}
"""

# Behaviourally diverse children so PE's KMeans can form clusters, plus
# distinct return values so the evaluator scores them differently.
SEED_CHILDREN = (
    _scaffold("def f():\n    return 2\n"),
    _scaffold("def f():\n    return sum(i for i in range(3))\n"),
    _scaffold("def f():\n    t=0\n    for i in range(4):\n        t+=i\n    return t\n"),
    _scaffold("def f():\n    return [x for x in range(5)][0]\n"),
    _scaffold("def f():\n    return 6\n"),
)


class SpyCoordination:
    """Delegates to a real arm; records which hooks the host called."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def sampling_request(self, ctx):
        self.calls.append("sampling_request")
        return self.inner.sampling_request(ctx)

    async def advise(self, ctx):
        self.calls.append("advise")
        return await self.inner.advise(ctx)

    def report_result(self, ctx, child, attribution, eval_failed, *, outcome=None):
        self.calls.append("report_result")
        kwargs = {}
        if outcome is not None:
            kwargs["outcome"] = outcome
        return self.inner.report_result(
            ctx, child, attribution, eval_failed, **kwargs
        )

    async def retry_advice(self, ctx, error_text, attempt):
        self.calls.append("retry_advice")
        return await self.inner.retry_advice(ctx, error_text, attempt)

    async def on_generation_end(self, ctx):
        self.calls.append("on_generation_end")
        return await self.inner.on_generation_end(ctx)

    def state_dict(self):
        return self.inner.state_dict()

    def load_state_dict(self, state):
        return self.inner.load_state_dict(state)

    def log_snapshot(self):
        return self.inner.log_snapshot()

    @property
    def llm(self):
        return getattr(self.inner, "llm", None)


def make_chat_llm(content: str):
    """Fake chat-completions LLM for PES / HiFo extraction."""

    async def create(**params):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )

    return BudgetedLLM(
        model="fake-model",
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        account="coordination",
        tag="arm_sweep.coordination",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
        retries=0,
        retry_delay=0.0,
    )


class GenerateLLM:
    """Fake ``.generate`` LLM for PE (paradigm / variant proposals)."""

    def __init__(self):
        self.n = 0

    async def generate(self, prompt, **kw):
        self.n += 1
        return f"```python\ndef f():\n    return {10 + self.n}\n```"


def make_config(**overrides) -> NoemaConfig:
    defaults = dict(
        max_iterations=20,
        checkpoint_interval=100,
        database=DatabaseConfig(
            in_memory=True,
            num_islands=2,
            population_size=50,
            random_seed=42,
            migration_interval=1000,
        ),
        evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
        budget=BudgetConfig(total_tokens=1_000_000),
        # Bandit steers this menu; leaving it on is harmless for other arms.
        mutation_operators=["e1", "e2", "m1", "m2", "m3"],
    )
    defaults.update(overrides)
    return NoemaConfig(**defaults)


def build_arm(key: str):
    """Instantiate a registry arm with whatever fake LLM that arm actually uses."""
    if key.startswith("pes"):
        llm = make_chat_llm(
            "## Plan Outline 1\nImprove the return value.\n"
            "### Final Child Solution Generation Plan\nReturn a larger number."
        )
        return build_coordination_module(key, {}, llm=llm, rng=random.Random(0))
    if key == "pe":
        return build_coordination_module(
            key,
            {
                "interval": 1,  # fire on every generation tick
                "n_clusters": 3,
                "n_variants": 1,
                "domain_context": "Maximise the return value.",
            },
            llm=GenerateLLM(),
            rng=random.Random(0),
        )
    if key == "hifo":
        return build_coordination_module(
            key, {}, llm=make_chat_llm("Tip: prefer fewer loops."), rng=random.Random(0)
        )
    return build_coordination_module(key, {}, llm=None, rng=random.Random(0))


def make_session(tmp, key: str, stop_children: int = 4):
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as f:
        f.write(EVAL_SCRIPT)
    spy = SpyCoordination(build_arm(key))
    session = AgentSession(
        config=make_config(),
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        coordination=spy,
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        stop_children=stop_children,
        task="Maximise the number the program returns.",
    )
    return session, spy


async def drive_child(session, code):
    session.next_target()
    parent = session.select_parent()
    brief = await session.get_brief()
    result = await session.submit_child(code=code)
    return parent, brief, result


class TestEveryArmFiresPerChildHooks(unittest.TestCase):
    """The host must call sampling_request → advise → report_result for every arm."""

    def test_every_registry_arm_participates_in_one_accepted_child(self):
        for key in sorted(MODULE_REGISTRY):
            with self.subTest(arm=key):
                with tempfile.TemporaryDirectory() as tmp:
                    session, spy = make_session(tmp, key)

                    async def scenario():
                        await session.begin_run()
                        return await drive_child(
                            session, _scaffold("def f():\n    return 7\n")
                        )

                    parent, brief, result = asyncio.run(scenario())

                    self.assertEqual(result["status"], "accepted", key)
                    self.assertEqual(
                        spy.calls,
                        ["sampling_request", "advise", "report_result"],
                        key,
                    )
                    self.assertIn("parent_id", parent)
                    self.assertIn("brief", brief)
                    self.assertTrue(brief["brief"])


class TestEveryArmGetsAGenerationTick(unittest.TestCase):
    """A forgetful agent must not starve any arm of ``on_generation_end``."""

    def test_every_registry_arm_receives_the_host_fired_tick(self):
        for key in sorted(MODULE_REGISTRY):
            with self.subTest(arm=key):
                with tempfile.TemporaryDirectory() as tmp:
                    session, spy = make_session(tmp, key, stop_children=4)
                    cadence = session.substrate.steps_per_generation

                    async def scenario():
                        await session.begin_run()
                        for n in range(cadence):
                            await drive_child(session, SEED_CHILDREN[n])

                    asyncio.run(scenario())
                    self.assertIn("on_generation_end", spy.calls, key)


class TestBanditSurfacesItsOperatorChoice(unittest.TestCase):
    """Bandit is zero-token: its only signal is which operator to use. That
    choice must reach the agent somehow, or the arm is inert under this host."""

    def test_select_parent_returns_the_operator_the_bandit_drew(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, "bandit")

            async def scenario():
                await session.begin_run()
                session.next_target()
                return session.select_parent()

            parent = asyncio.run(scenario())
            self.assertIn(parent["operator"], ["e1", "e2", "m1", "m2", "m3"])


class TestPEProposalsAreHostEvaluated(unittest.TestCase):
    """PE authors whole programs on the generation tick. The host — not the
    agent — must evaluate and insert them, or the arm cannot transfer."""

    def test_pe_proposals_land_in_the_store_after_the_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_session(tmp, "pe", stop_children=6)
            cadence = session.substrate.steps_per_generation
            n_agent = max(cadence, 4)

            async def scenario():
                await session.begin_run()
                for n in range(n_agent):
                    await drive_child(session, SEED_CHILDREN[n % len(SEED_CHILDREN)])
                return session.store.num_programs

            n_programs = asyncio.run(scenario())
            self.assertGreater(
                n_programs,
                1 + n_agent,
                "PE fired on_generation_end but no proposal was inserted",
            )
            self.assertIn("on_generation_end", spy.calls)


class TestBriefCarriesArmGuidance(unittest.TestCase):
    """Coordination-OFF vs coordination-ON must stay distinguishable in the brief."""

    def test_null_leaves_no_coordination_section(self):
        brief = asyncio.run(self._brief_for("null"))
        self.assertNotIn(COORDINATION_HEADER, brief)

    def test_hifo_and_pes_custom_put_guidance_under_the_delimiter(self):
        for key in ("hifo", "pes-custom"):
            with self.subTest(arm=key):
                brief = asyncio.run(self._brief_for(key))
                self.assertIn(COORDINATION_HEADER, brief, key)
                after = brief.split(COORDINATION_HEADER, 1)[1].strip()
                self.assertTrue(after, key)

    def test_pes_faithful_uses_full_executor_prompt_without_suffix_header(self):
        # Same as NoemaController: full_executor_prompt skips build_mutation_prompt
        # / inject_advice; the advice IS the user prompt.
        brief = asyncio.run(self._brief_for("pes-faithful"))
        self.assertNotIn(COORDINATION_HEADER, brief)
        self.assertIn("# Plan", brief)
        self.assertIn("Parent Solution", brief)

    async def _brief_for(self, key: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, key)
            await session.begin_run()
            _, brief, _ = await drive_child(
                session, _scaffold("def f():\n    return 7\n")
            )
            return brief["brief"]
