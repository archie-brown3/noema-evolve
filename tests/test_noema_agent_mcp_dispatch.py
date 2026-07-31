"""B1/B3: the serialised iteration driven through the MCP tool surface (task 0160).

Same host, same arms — but every call goes through ``mcp_server.dispatch`` by
tool name, the way an outer agent's MCP client would. Two questions only:
does phase order hold at the tool boundary, and do the coordination arms still
participate when nobody calls ``AgentSession`` methods directly?
"""

import asyncio
import os
import random
import tempfile
import unittest

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.agenthost import AgentSession
from noema.agenthost.mcp_server import HANDLERS, TOOLS, dispatch
from noema.agenthost.mutation import FakeMutationBackend
from noema.budget.ledger import TokenLedger
from noema.config import BudgetConfig, NoemaConfig
from noema.coordination import MODULE_REGISTRY, build_coordination_module

from tests.test_noema_agent_arm_sweep import (
    EVAL_SCRIPT,
    INITIAL_PROGRAM,
    SEED_CHILDREN,
    SpyCoordination,
    build_arm,
    _scaffold,
)


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
        mutation_operators=["e1", "e2", "m1", "m2", "m3"],
    )
    defaults.update(overrides)
    return NoemaConfig(**defaults)


def make_session(tmp, key: str, *, codes, stop_children: int = 4):
    """A session whose only mutation source is a Fake backend of canned children."""
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as f:
        f.write(EVAL_SCRIPT)
    spy = SpyCoordination(build_arm(key))
    supply = iter(codes)
    session = AgentSession(
        config=make_config(),
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        coordination=spy,
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        stop_children=stop_children,
        task="Maximise the number the program returns.",
        mutation_backend=FakeMutationBackend(producer=lambda _req: next(supply)),
    )
    return session, spy


async def drive_child_via_tools(session):
    """One target cycle, called only by tool name — no session methods."""
    await dispatch(session, "next_target")
    parent = await dispatch(session, "select_parent")
    brief = await dispatch(session, "get_brief")
    result = await dispatch(session, "run_mutation")
    return parent, brief, result


class TestToolSurface(unittest.TestCase):
    def test_serialised_iteration_tools_are_registered(self):
        self.assertEqual(
            [tool["name"] for tool in TOOLS],
            [
                "begin_run",
                "next_target",
                "select_parent",
                "get_brief",
                "run_mutation",
                "run_status",
                "run_until_budget",
            ],
        )
        self.assertEqual(set(HANDLERS), {tool["name"] for tool in TOOLS})
        for tool in TOOLS:
            self.assertTrue(tool["description"].strip(), tool["name"])
            self.assertFalse(
                tool["parameters"].get("required"),
                f"{tool['name']} must not require arguments: the session holds state",
            )

    def test_unknown_tool_is_a_programming_error_not_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, "null", codes=[INITIAL_PROGRAM])
            with self.assertRaises(KeyError):
                asyncio.run(dispatch(session, "delete_population"))


class TestPhaseGateAtTheToolBoundary(unittest.TestCase):
    """Out-of-order tool calls must name the call the agent owes, not raise."""

    def test_each_premature_tool_call_is_refused_with_the_required_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(
                tmp, "null", codes=[_scaffold("def f():\n    return 7\n")]
            )

            async def scenario():
                refusals = {}
                for tool in ("select_parent", "get_brief", "run_mutation"):
                    refusals[("idle", tool)] = await dispatch(session, tool)
                await dispatch(session, "begin_run")
                for tool in ("select_parent", "get_brief", "run_mutation"):
                    refusals[("open", tool)] = await dispatch(session, tool)
                await dispatch(session, "next_target")
                for tool in ("get_brief", "run_mutation"):
                    refusals[("targeted", tool)] = await dispatch(session, tool)
                await dispatch(session, "select_parent")
                refusals[("parented", "run_mutation")] = await dispatch(
                    session, "run_mutation"
                )
                return refusals

            refusals = asyncio.run(scenario())

            for (phase, tool), result in refusals.items():
                self.assertEqual(result["status"], "refused", (phase, tool))
                self.assertEqual(result["attempted"], tool, (phase, tool))
            self.assertEqual(refusals[("idle", "run_mutation")]["required_call"], "begin_run")
            self.assertEqual(refusals[("open", "get_brief")]["required_call"], "next_target")
            self.assertEqual(
                refusals[("targeted", "run_mutation")]["required_call"], "select_parent"
            )
            self.assertEqual(
                refusals[("parented", "run_mutation")]["required_call"], "get_brief"
            )
            # A refused call must not have advanced the run.
            status = asyncio.run(dispatch(session, "run_status"))
            self.assertEqual(status["children_accepted"], 0)

    def test_run_status_is_readable_before_the_run_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, "null", codes=[INITIAL_PROGRAM])
            status = asyncio.run(dispatch(session, "run_status"))
            self.assertEqual(status["children_accepted"], 0)
            self.assertFalse(status["stopped"])


class TestDrivingTheRunThroughToolsOnly(unittest.TestCase):
    def test_a_tool_only_client_accepts_children_until_the_run_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(
                tmp, "null", codes=list(SEED_CHILDREN), stop_children=3
            )

            async def scenario():
                await dispatch(session, "begin_run")
                statuses = []
                while True:
                    target = await dispatch(session, "next_target")
                    if target.get("status") == "complete":
                        break
                    await dispatch(session, "select_parent")
                    await dispatch(session, "get_brief")
                    statuses.append((await dispatch(session, "run_mutation"))["status"])
                return statuses, await dispatch(session, "run_status")

            statuses, status = asyncio.run(scenario())

            self.assertEqual(statuses, ["accepted"] * 3)
            self.assertTrue(status["stopped"])
            self.assertEqual(status["children_accepted"], 3)
            self.assertEqual(session.store.num_programs, 4)  # initial + 3

    def test_select_parent_reports_operator_and_inspirations_to_the_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, "null", codes=list(SEED_CHILDREN))

            async def scenario():
                await dispatch(session, "begin_run")
                await dispatch(session, "next_target")
                return await dispatch(session, "select_parent")

            parent = asyncio.run(scenario())

            self.assertIn("parent_id", parent)
            self.assertIn("parent_code", parent)
            self.assertIn(parent["operator"], ["e1", "e2", "m1", "m2", "m3"])
            self.assertIsInstance(parent["inspirations"], list)


class TestEveryArmParticipatesThroughTheToolSurface(unittest.TestCase):
    """B3: same hook participation as the direct arm sweep, via dispatch."""

    def test_every_registry_arm_fires_its_per_child_hooks(self):
        for key in sorted(MODULE_REGISTRY):
            with self.subTest(arm=key):
                with tempfile.TemporaryDirectory() as tmp:
                    session, spy = make_session(
                        tmp, key, codes=[_scaffold("def f():\n    return 7\n")]
                    )

                    async def scenario():
                        await dispatch(session, "begin_run")
                        return await drive_child_via_tools(session)

                    parent, brief, result = asyncio.run(scenario())

                    self.assertEqual(result["status"], "accepted", key)
                    self.assertEqual(
                        spy.calls,
                        ["sampling_request", "advise", "report_result"],
                        key,
                    )
                    self.assertTrue(brief["brief"], key)
                    self.assertIn("mutation", result, key)

    def test_every_registry_arm_receives_the_host_fired_generation_tick(self):
        for key in sorted(MODULE_REGISTRY):
            with self.subTest(arm=key):
                with tempfile.TemporaryDirectory() as tmp:
                    session, spy = make_session(
                        tmp, key, codes=list(SEED_CHILDREN), stop_children=len(SEED_CHILDREN)
                    )
                    cadence = session.substrate.steps_per_generation

                    async def scenario():
                        await dispatch(session, "begin_run")
                        for _ in range(cadence):
                            await drive_child_via_tools(session)

                    asyncio.run(scenario())
                    self.assertIn("on_generation_end", spy.calls, key)


class TestPeProposalsStillHostOwnedUnderTools(unittest.TestCase):
    """PE authors programs on the tick; the host inserts them, not the agent."""

    def test_pe_proposals_land_in_the_store_when_driven_by_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_session(
                tmp, "pe", codes=list(SEED_CHILDREN), stop_children=len(SEED_CHILDREN)
            )
            n_children = len(SEED_CHILDREN)

            async def scenario():
                await dispatch(session, "begin_run")
                for _ in range(n_children):
                    await drive_child_via_tools(session)
                return session.store.num_programs

            n_programs = asyncio.run(scenario())

            self.assertIn("on_generation_end", spy.calls)
            self.assertGreater(
                n_programs,
                1 + n_children,
                "PE fired on_generation_end but no proposal was inserted",
            )


class TestRunUntilBudget(unittest.TestCase):
    def test_run_until_budget_accepts_children_until_stopped(self):
        stop = 3
        codes = [_scaffold(f"def f():\n    return {n}\n") for n in (2, 3, 4)]
        with tempfile.TemporaryDirectory() as tmp:
            session, spy = make_session(tmp, "null", codes=codes, stop_children=stop)

            async def scenario():
                return await dispatch(session, "run_until_budget")

            status = asyncio.run(scenario())

            self.assertTrue(status["stopped"])
            self.assertEqual(status["children_accepted"], stop)
            self.assertEqual(session.store.num_programs, stop + 1)
            self.assertIn("sampling_request", spy.calls)
            self.assertIn("advise", spy.calls)
            self.assertIn("report_result", spy.calls)

    def test_run_until_budget_refused_mid_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(
                tmp, "null", codes=[_scaffold("def f():\n    return 7\n")]
            )

            async def scenario():
                await dispatch(session, "begin_run")
                await dispatch(session, "next_target")
                await dispatch(session, "select_parent")
                await dispatch(session, "get_brief")
                return await dispatch(session, "run_until_budget")

            result = asyncio.run(scenario())

            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["attempted"], "run_until_budget")
            self.assertEqual(result["required_call"], "run_mutation")

    def test_run_until_budget_idempotent_on_complete(self):
        stop = 1
        codes = [_scaffold("def f():\n    return 2\n")]
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(tmp, "null", codes=codes, stop_children=stop)

            async def scenario():
                first = await dispatch(session, "run_until_budget")
                second = await dispatch(session, "run_until_budget")
                return first, second, session.store.num_programs

            first, second, n_programs = asyncio.run(scenario())

            self.assertTrue(first["stopped"])
            self.assertTrue(second["stopped"])
            self.assertEqual(first["children_accepted"], stop)
            self.assertEqual(second["children_accepted"], stop)
            self.assertEqual(n_programs, stop + 1)


if __name__ == "__main__":
    unittest.main()
