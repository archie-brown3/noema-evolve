"""Behaviour tests for the agent-driven host (side research, task 0160).

Seams under test, confirmed before writing any test:
  1. ``AgentSession``'s public tool-handler methods
  2. ``render_brief``
  3. the MCP adapter's tool mapping and error serialisation

There is no mutation LLM here by design: in this host the agent harness *is* the
mutation layer, so tests submit finished child code exactly as a tool call would.
Coordination, substrate, and evaluator internals are covered by their own suites;
these tests only assert that the host drives them.
"""

import asyncio
import os
import tempfile
import unittest

from noema.agenthost import AgentSession, PhaseError, render_brief
from noema.budget.ledger import TokenLedger
from noema.config import BudgetConfig, NoemaConfig
from noema.coordination import Advice, NullCoordination, Outcome
from noema.evolution.prompts import COORDINATION_HEADER

from openevolve.config import DatabaseConfig, EvaluatorConfig

INITIAL_PROGRAM = "def f():\n    return 1\n"

# Scores a program by the number it returns: `return 7` -> 0.7. A program that
# returns nothing is a failed evaluation, signalled the way the metrics contract
# requires (a reserved "error" key).
EVAL_SCRIPT = """\
import re

def evaluate(program_path):
    with open(program_path) as f:
        code = f.read()
    m = re.search(r"return (\\d+(?:\\.\\d+)?)", code)
    if m is None:
        return {"error": "program returns no value"}
    return {"combined_score": min(1.0, float(m.group(1)) / 10.0)}
"""


class RecordingCoordination(NullCoordination):
    """Null arm that records which hooks the host fired, and advises real text."""

    PROMPT_BLOCK = "Reduce the number of loops."

    def __init__(self):
        super().__init__()
        self.calls = []

    async def advise(self, ctx):
        self.calls.append(("advise", ctx.iteration))
        return Advice(prompt_block=self.PROMPT_BLOCK, attribution={"slice": "1"})

    def report_result(self, ctx, child, attribution, eval_failed, *, outcome=Outcome.ACCEPTED):
        self.calls.append(
            ("report_result", None if child is None else child.id, eval_failed, outcome)
        )

    async def on_generation_end(self, ctx):
        self.calls.append(("on_generation_end", ctx.generation))
        return None


class RetryAdvisingCoordination(RecordingCoordination):
    """An arm that has something to say about a failure (PES is the real case)."""

    RETRY_BLOCK = "The previous attempt returned nothing; return a number."

    async def retry_advice(self, ctx, error_text, attempt):
        self.calls.append(("retry_advice", attempt))
        return self.RETRY_BLOCK


def make_config(**overrides) -> NoemaConfig:
    defaults = dict(
        max_iterations=6,
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
    )
    defaults.update(overrides)
    return NoemaConfig(**defaults)


async def drive_child(session, code):
    """One full target cycle, the way a compliant agent would drive it."""
    session.next_target()
    session.select_parent()
    await session.get_brief()
    return await session.submit_child(code=code)


def make_session(tmp, coordination=None, stop_children=4, config=None):
    eval_path = os.path.join(tmp, "evaluator.py")
    if not os.path.exists(eval_path):
        with open(eval_path, "w") as f:
            f.write(EVAL_SCRIPT)
    coordination = coordination if coordination is not None else RecordingCoordination()
    session = AgentSession(
        config=config or make_config(),
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        coordination=coordination,
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        stop_children=stop_children,
    )
    return session, coordination


class TestSubmitChild(unittest.TestCase):
    def test_submitted_child_is_evaluated_stored_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, coordination = make_session(tmp)

            async def scenario():
                await session.begin_run()
                session.next_target()
                session.select_parent()
                await session.get_brief()
                return await session.submit_child(code="def f():\n    return 7\n")

            result = asyncio.run(scenario())

            self.assertEqual(result["status"], "accepted")
            self.assertAlmostEqual(result["metrics"]["combined_score"], 0.7)
            self.assertEqual(session.children_accepted, 1)

            stored_ids = [program.id for program in session.store.population()]
            self.assertIn(result["program_id"], stored_ids)

            reported = [call for call in coordination.calls if call[0] == "report_result"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0][1], result["program_id"])
            self.assertFalse(reported[0][2])


class TestCallOrder(unittest.TestCase):
    """The host, not the agent, owns the state machine: an agent that skips a
    step is told which call it owes rather than silently mutating the run."""

    def test_a_premature_call_is_refused_and_names_the_required_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, coordination = make_session(tmp)
            child = "def f():\n    return 9\n"

            async def scenario():
                await session.begin_run()
                refusals = {}

                for premature, expected in (
                    ("select_parent", "next_target"),
                    ("submit_child", "next_target"),
                ):
                    refusals[premature] = await self.refuse(session, premature, child)
                    self.assertEqual(refusals[premature].required_call, expected)

                session.next_target()
                self.assertEqual(
                    (await self.refuse(session, "submit_child", child)).required_call,
                    "select_parent",
                )

                session.select_parent()
                self.assertEqual(
                    (await self.refuse(session, "submit_child", child)).required_call,
                    "get_brief",
                )

            asyncio.run(scenario())

            # No refused call may leave a trace: population untouched, no advice
            # or result reported.
            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            self.assertEqual(session.children_accepted, 0)
            self.assertNotIn("report_result", [call[0] for call in coordination.calls])

    async def refuse(self, session, call, child):
        with self.assertRaises(PhaseError) as raised:
            if call == "submit_child":
                await session.submit_child(code=child)
            else:
                getattr(session, call)()
        return raised.exception


class TestStopCondition(unittest.TestCase):
    """The run ends on a fixed count of accepted children, not on agent judgement."""

    def test_the_run_stops_after_the_configured_number_of_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, stop_children=2)

            async def scenario():
                await session.begin_run()
                await drive_child(session, "def f():\n    return 3\n")
                await drive_child(session, "def f():\n    return 4\n")
                return session.next_target()

            after_stop = asyncio.run(scenario())

            self.assertEqual(after_stop["status"], "complete")
            self.assertEqual(session.children_accepted, 2)
            self.assertTrue(session.run_status()["stopped"])

            with self.assertRaises(PhaseError):
                asyncio.run(session.submit_child(code="def f():\n    return 9\n"))

            self.assertEqual(len(session.store.population()), 3)  # initial + 2


class TestRejection(unittest.TestCase):
    """A child that fails evaluation never enters the population, and the agent
    gets another attempt at the same target rather than a fresh parent."""

    def test_a_failed_child_is_rejected_with_a_retry_brief_and_keeps_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, coordination = make_session(
                tmp, coordination=RetryAdvisingCoordination()
            )

            async def scenario():
                await session.begin_run()
                session.next_target()
                parent = session.select_parent()
                await session.get_brief()
                rejected = await session.submit_child(code="def f():\n    pass\n")
                # Resubmitting straight away must work: the target is still open.
                accepted = await session.submit_child(code="def f():\n    return 5\n")
                return parent, rejected, accepted

            parent, rejected, accepted = asyncio.run(scenario())

            self.assertEqual(rejected["status"], "rejected")
            self.assertIn(
                RetryAdvisingCoordination.RETRY_BLOCK, rejected["retry_brief"]
            )
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(session.children_accepted, 1)

            stored_ids = [program.id for program in session.store.population()]
            self.assertCountEqual(stored_ids, ["initial", accepted["program_id"]])
            self.assertEqual(accepted["parent_id"], parent["parent_id"])

            self.assertEqual(
                [call for call in coordination.calls if call[0] == "report_result"],
                [
                    ("report_result", None, True, Outcome.EVAL_ERROR),
                    ("report_result", accepted["program_id"], False, Outcome.ACCEPTED),
                ],
            )


class TestGenerationTick(unittest.TestCase):
    """The generation boundary is the host's job: a forgetful agent must not be
    able to starve an arm of its `on_generation_end` hook."""

    def test_the_tick_fires_on_the_substrate_cadence_without_the_agent_asking(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, coordination = make_session(tmp, stop_children=4)
            cadence = session.substrate.steps_per_generation

            async def scenario():
                await session.begin_run()
                for n in range(cadence):
                    await drive_child(session, f"def f():\n    return {n + 2}\n")

            asyncio.run(scenario())

            self.assertEqual(
                [call for call in coordination.calls if call[0] == "on_generation_end"],
                [("on_generation_end", 1)],
            )
            self.assertEqual(session.generation, 1)
            # Initial scores 0.1; the two children score 0.2 and 0.3.
            self.assertAlmostEqual(session.best_fitness_history[-1], 0.3)
            self.assertAlmostEqual(
                session.avg_fitness_history[-1], (0.1 + 0.2 + 0.3) / 3
            )


class TestBrief(unittest.TestCase):
    """The brief is the whole prompt surface in this host: it carries the arm's
    advice under the same delimiter the controller uses, and carries none when
    the arm has nothing to say (the coordination-OFF controlled variable)."""

    TASK = "Maximise the number the program returns."

    def test_an_arms_advice_is_carried_under_the_coordination_delimiter(self):
        brief = render_brief(
            task=self.TASK,
            parent_code=INITIAL_PROGRAM,
            parent_metrics={"combined_score": 0.1},
            coordination_block="Reduce the number of loops.",
        )

        self.assertIn(COORDINATION_HEADER, brief)
        self.assertIn("Reduce the number of loops.", brief)
        # The agent must be able to act on the brief alone, without more tool calls.
        self.assertIn(self.TASK, brief)
        self.assertIn("return 1", brief)
        self.assertIn("0.1", brief)

    def test_an_arm_with_nothing_to_say_leaves_no_coordination_section(self):
        brief = render_brief(
            task=self.TASK,
            parent_code=INITIAL_PROGRAM,
            parent_metrics={"combined_score": 0.1},
            coordination_block="",
        )

        self.assertNotIn(COORDINATION_HEADER, brief)
        self.assertIn(self.TASK, brief)

    def test_the_session_hands_the_agent_the_rendered_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp)

            async def scenario():
                await session.begin_run()
                session.next_target()
                session.select_parent()
                return await session.get_brief()

            brief = asyncio.run(scenario())["brief"]

            self.assertIn(RecordingCoordination.PROMPT_BLOCK, brief)
            self.assertIn("return 1", brief)  # the parent's code
