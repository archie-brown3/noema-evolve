"""Behaviour tests for AgentSession.run_mutation (task 0160).

Pins the mutation → evaluate → store pipeline behind MutationBackend.
No live coding CLI: FakeMutationBackend for host orchestration; a stub
executable for CliMutationBackend's file+exit contract.
"""

import asyncio
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from noema.agenthost import AgentSession, PhaseError
from noema.agenthost.mutation import (
    CliMutationBackend,
    FakeMutationBackend,
    MutationRequest,
    MutationResult,
    mutation_layout,
)
from noema.budget.ledger import TokenLedger
from noema.config import BudgetConfig, NoemaConfig
from noema.coordination import Advice, NullCoordination, Outcome

from openevolve.config import DatabaseConfig, EvaluatorConfig

INITIAL_PROGRAM = "def f():\n    return 1\n"

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

    async def retry_advice(self, ctx, error_text, attempt):
        self.calls.append(("retry_advice", attempt))
        return f"retry after: {error_text}"


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


def make_session(tmp, *, mutation_backend, coordination=None, stop_children=4):
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as f:
        f.write(EVAL_SCRIPT)
    coordination = coordination if coordination is not None else RecordingCoordination()
    session = AgentSession(
        config=make_config(),
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "output"),
        coordination=coordination,
        ledger=TokenLedger(total_budget_tokens=1_000_000),
        stop_children=stop_children,
        mutation_backend=mutation_backend,
    )
    return session, coordination


async def briefed_session(session):
    await session.begin_run()
    session.next_target()
    session.select_parent()
    await session.get_brief()


class TestRunMutationAccept(unittest.TestCase):
    def test_fake_backend_child_is_evaluated_stored_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code="def f():\n    return 7\n")
            session, coordination = make_session(tmp, mutation_backend=backend)

            result = asyncio.run(self._run(session))

            self.assertEqual(result["status"], "accepted")
            self.assertAlmostEqual(result["metrics"]["combined_score"], 0.7)
            self.assertIn("mutation", result)
            self.assertEqual(session.children_accepted, 1)
            self.assertIn(result["program_id"], [p.id for p in session.store.population()])
            reported = [c for c in coordination.calls if c[0] == "report_result"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0][1], result["program_id"])

    async def _run(self, session):
        await briefed_session(session)
        return await session.run_mutation()


class TestRunMutationRejectAndRetry(unittest.TestCase):
    def test_bad_code_rejects_then_second_run_mutation_retries_same_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            codes = iter(["def f():\n    pass\n", "def f():\n    return 5\n"])
            backend = FakeMutationBackend(producer=lambda _req: next(codes))
            session, coordination = make_session(tmp, mutation_backend=backend)

            async def scenario():
                await briefed_session(session)
                rejected = await session.run_mutation()
                accepted = await session.run_mutation()
                return rejected, accepted

            rejected, accepted = asyncio.run(scenario())

            self.assertEqual(rejected["status"], "rejected")
            self.assertIn("retry_brief", rejected)
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(session.children_accepted, 1)
            self.assertCountEqual(
                [p.id for p in session.store.population()],
                ["initial", accepted["program_id"]],
            )
            self.assertEqual(
                [c for c in coordination.calls if c[0] == "report_result"],
                [
                    ("report_result", None, True, Outcome.EVAL_ERROR),
                    ("report_result", accepted["program_id"], False, Outcome.ACCEPTED),
                ],
            )


class TestRunMutationBackendFailure(unittest.TestCase):
    def test_backend_failure_does_not_touch_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(fail_error="deliverable missing")
            session, coordination = make_session(tmp, mutation_backend=backend)

            async def scenario():
                await briefed_session(session)
                return await session.run_mutation()

            result = asyncio.run(scenario())

            self.assertEqual(result["status"], "mutation_failed")
            self.assertEqual(result["error"], "deliverable missing")
            self.assertEqual(result["required_call"], "run_mutation")
            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            self.assertEqual(session.children_accepted, 0)
            self.assertNotIn(
                "report_result", [c[0] for c in coordination.calls]
            )
            # Phase stays briefed so the outer caller can retry.
            self.assertEqual(session._phase, "briefed")


class TestRunMutationPhaseGate(unittest.TestCase):
    def test_run_mutation_before_brief_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(
                tmp, mutation_backend=FakeMutationBackend(code="def f():\n    return 1\n")
            )

            async def scenario():
                await session.begin_run()
                with self.assertRaises(PhaseError) as raised:
                    await session.run_mutation()
                self.assertEqual(raised.exception.required_call, "next_target")

                session.next_target()
                with self.assertRaises(PhaseError) as raised:
                    await session.run_mutation()
                self.assertEqual(raised.exception.required_call, "select_parent")

                session.select_parent()
                with self.assertRaises(PhaseError) as raised:
                    await session.run_mutation()
                self.assertEqual(raised.exception.required_call, "get_brief")

            asyncio.run(scenario())
            self.assertEqual([p.id for p in session.store.population()], ["initial"])


class TestCliMutationBackendContract(unittest.TestCase):
    def test_stub_executable_writing_child_is_read_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub_mutator.sh"
            stub.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    # Host sets MUTATION_DELIVERABLE to the expected child path.
                    printf '%s\\n' 'def f():' '    return 8' > "$MUTATION_DELIVERABLE"
                    exit 0
                    """
                )
            )
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

            work = Path(tmp) / "work"
            work.mkdir()
            deliverable = work / "child.py"
            backend = CliMutationBackend(command=[str(stub)])
            result = backend.run(
                MutationRequest(
                    prompt={"system": "sys", "user": "improve the parent"},
                    parent_code=INITIAL_PROGRAM,
                    work_dir=work,
                    deliverable_path=deliverable,
                    timeout_s=5.0,
                )
            )

            self.assertTrue(result.ok)
            self.assertIn("return 8", result.code)
            self.assertEqual(result.backend_trace.get("exit_code"), 0)

    def test_missing_deliverable_is_mutation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub_noop.sh"
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

            work = Path(tmp) / "work"
            work.mkdir()
            backend = CliMutationBackend(command=[str(stub)])
            result = backend.run(
                MutationRequest(
                    prompt={"system": "", "user": "brief"},
                    parent_code=INITIAL_PROGRAM,
                    work_dir=work,
                    deliverable_path=work / "child.py",
                    timeout_s=5.0,
                )
            )

            self.assertFalse(result.ok)
            self.assertIsNone(result.code)
            # Deliverable is seeded with the parent; a no-op CLI leaves it unchanged.
            self.assertIn("unchanged", result.error.lower())


class TestMutationLayoutIsHostOwned(unittest.TestCase):
    def test_paths_are_deterministic_under_output_dir(self):
        layout = mutation_layout("/runs/demo", 42, 3, file_suffix=".py")
        self.assertEqual(
            layout.work_dir,
            Path("/runs/demo/mutations/it000042/m03"),
        )
        self.assertEqual(
            layout.deliverable_path,
            Path("/runs/demo/mutations/it000042/m03/child.py"),
        )
        self.assertEqual(
            mutation_layout("/runs/demo", 42, 3).deliverable_path,
            layout.deliverable_path,
        )

    def test_run_mutation_writes_deliverable_under_output_dir_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code="def f():\n    return 7\n")
            session, _ = make_session(tmp, mutation_backend=backend)

            result = asyncio.run(self._run(session))

            expected = (
                Path(session.output_dir)
                / "mutations"
                / "it000000"
                / "m01"
                / "child.py"
            )
            self.assertEqual(result["mutation"]["deliverable"], str(expected))
            self.assertTrue(expected.is_file())
            self.assertIn("return 7", expected.read_text())

    def test_brief_is_controller_assembled_prompt_not_a_path_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code="def f():\n    return 7\n")
            session, _ = make_session(tmp, mutation_backend=backend)

            async def drive():
                await session.begin_run()
                session.next_target()
                session.select_parent()
                return await session.get_brief()

            result = asyncio.run(drive())
            self.assertIn("system", result["prompt"])
            self.assertIn("user", result["prompt"])
            # Default config is diff_based_evolution=True → SEARCH/REPLACE strategy.
            self.assertIn("<<<<<<< SEARCH", result["brief"])
            self.assertNotIn("child.py", result["brief"])
            self.assertNotIn("MUTATION_DELIVERABLE", result["brief"])
            self.assertIn(RecordingCoordination.PROMPT_BLOCK, result["brief"])

    async def _run(self, session):
        await briefed_session(session)
        return await session.run_mutation()


class TestMcpRunMutationAdapter(unittest.TestCase):
    def test_wrong_phase_returns_structured_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(
                tmp,
                mutation_backend=FakeMutationBackend(code="def f():\n    return 1\n"),
            )

            async def scenario():
                await session.begin_run()
                from noema.agenthost.mcp_server import run_mutation as mcp_run_mutation

                return await mcp_run_mutation(session)

            result = asyncio.run(scenario())
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["required_call"], "next_target")


class TestMaterializeThroughRunMutation(unittest.TestCase):
    """A1: Fake backend can emit SEARCH/REPLACE; admitted child matches parse."""

    def test_diff_deliverable_is_applied_before_admission(self):
        diff_response = (
            "<<<<<<< SEARCH\n"
            "def f():\n    return 1\n"
            "=======\n"
            "def f():\n    return 7\n"
            ">>>>>>> REPLACE\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code=diff_response)
            session, _ = make_session(tmp, mutation_backend=backend)

            result = asyncio.run(self._run(session))

            self.assertEqual(result["status"], "accepted")
            self.assertAlmostEqual(result["metrics"]["combined_score"], 0.7)
            child = next(p for p in session.store.population() if p.id != "initial")
            self.assertIn("return 7", child.code)
            self.assertNotIn("SEARCH", child.code)

    async def _run(self, session):
        await briefed_session(session)
        return await session.run_mutation()


class TestAttemptTraceAndArtifacts(unittest.TestCase):
    """A2: failing mutations leave attempt_trace rows; accept stores artifacts."""

    def test_backend_failure_writes_trace_without_population_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(fail_error="deliverable missing")
            session, _ = make_session(tmp, mutation_backend=backend)

            result = asyncio.run(self._run(session))

            self.assertEqual(result["status"], "mutation_failed")
            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            trace_path = Path(session.output_dir) / "attempt_trace.jsonl"
            self.assertTrue(trace_path.is_file())
            rows = [
                __import__("json").loads(line)
                for line in trace_path.read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["mode"], "agent_session")
            self.assertEqual(rows[0]["outcome"], "provider_failure")
            self.assertIn("deliverable", rows[0]["mutation"])

    def test_unparseable_diff_writes_trace_without_population_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Diff mode with empty SEARCH blocks → materialize returns None-ish
            # only when text is blank; use whitespace-only to force None.
            backend = FakeMutationBackend(code="   \n")
            session, _ = make_session(tmp, mutation_backend=backend)

            result = asyncio.run(self._run(session))

            self.assertEqual(result["status"], "mutation_failed")
            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            rows = [
                __import__("json").loads(line)
                for line in (
                    Path(session.output_dir) / "attempt_trace.jsonl"
                ).read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(rows[0]["outcome"], "unparseable_response")
            self.assertEqual(rows[0]["mode"], "agent_session")

    async def _run(self, session):
        await briefed_session(session)
        return await session.run_mutation()


if __name__ == "__main__":
    unittest.main()
