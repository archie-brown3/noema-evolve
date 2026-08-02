"""Behaviour tests for agent-host mutation (task 0160, driver path since 0177).

Pins the mutation → evaluate → store pipeline behind MutationBackend, driven by
``run_agent_mode``. No live coding CLI: FakeMutationBackend for host
orchestration; a stub executable for CliMutationBackend's file+exit contract.
"""

import asyncio
import json
import os
import stat
import tempfile
import textwrap
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from openevolve.config import DatabaseConfig, EvaluatorConfig

from noema.agenthost.mutation import (
    CliMutationBackend,
    FakeMutationBackend,
    MutationRequest,
    MutationResult,
    mutation_layout,
)
from noema.agenthost.session import AgentSession
from noema.budget.cli_runner import CliRunner, CliRunResult
from noema.budget.ledger import TokenLedger
from noema.config import (
    BudgetConfig,
    CoordinationConfig,
    EscalationConfig,
    NoemaConfig,
)
from noema.coordination import Advice, NullCoordination, Outcome

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


def make_session(
    tmp,
    *,
    mutation_backend,
    coordination=None,
    stop_children=1,
    config=None,
):
    eval_path = os.path.join(tmp, "evaluator.py")
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
        mutation_backend=mutation_backend,
    )
    return session, coordination


def trace_rows(session):
    path = Path(session.output_dir) / "attempt_trace.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def selection_rows(session):
    path = Path(session.output_dir) / "selection_trace.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def child_of(session):
    return next(p for p in session.store.population() if p.id != "initial")


class TestRunMutationAccept(unittest.TestCase):
    def test_fake_backend_child_is_evaluated_stored_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code="def f():\n    return 7\n")
            session, coordination = make_session(tmp, mutation_backend=backend)

            asyncio.run(session.run_agent_mode())

            child = child_of(session)
            self.assertAlmostEqual(child.metrics["combined_score"], 0.7)
            self.assertEqual(session.children_accepted, 1)
            reported = [c for c in coordination.calls if c[0] == "report_result"]
            self.assertEqual(len(reported), 1)
            self.assertEqual(reported[0][1], child.id)


class TestRunMutationRejectAndRetry(unittest.TestCase):
    def test_bad_code_is_rejected_then_the_next_attempt_retries_the_same_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            codes = iter(["def f():\n    pass\n", "def f():\n    return 5\n"])
            backend = FakeMutationBackend(producer=lambda _req: next(codes))
            session, coordination = make_session(tmp, mutation_backend=backend)

            asyncio.run(session.run_agent_mode())

            child = child_of(session)
            self.assertEqual(session.children_accepted, 1)
            self.assertCountEqual([p.id for p in session.store.population()], ["initial", child.id])
            self.assertEqual(
                [c for c in coordination.calls if c[0] == "report_result"],
                [
                    ("report_result", None, True, Outcome.EVAL_ERROR),
                    ("report_result", child.id, False, Outcome.ACCEPTED),
                ],
            )
            traces = trace_rows(session)
            self.assertEqual([row["attempt"] for row in traces], [0, 1])
            self.assertNotEqual(traces[0]["attempt_id"], traces[1]["attempt_id"])
            selection = selection_rows(session)[0]
            self.assertEqual(selection["selected_attempt_id"], traces[1]["attempt_id"])
            self.assertEqual(child.metadata["source_attempt_id"], traces[1]["attempt_id"])


class TestRunMutationBackendFailure(unittest.TestCase):
    def test_backend_failure_does_not_touch_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(fail_error="deliverable missing")
            session, coordination = make_session(tmp, mutation_backend=backend)

            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(session.run_agent_mode())

            self.assertIn("deliverable missing", str(raised.exception))
            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            self.assertEqual(session.children_accepted, 0)
            self.assertNotIn("report_result", [c[0] for c in coordination.calls])


class TestCliMutationBackendContract(unittest.TestCase):
    def test_stub_executable_writing_child_is_read_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "stub_mutator.sh"
            stub.write_text(textwrap.dedent("""\
                    #!/bin/sh
                    # Host sets MUTATION_DELIVERABLE to the expected child path.
                    printf '%s\\n' 'def f():' '    return 8' > "$MUTATION_DELIVERABLE"
                    exit 0
                    """))
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

    def test_per_call_model_overrides_configured_cli_model(self):
        captured = {}

        def fake_run(_self, argv, **kwargs):
            captured["argv"] = argv
            kwargs["stdout_path"].write_text("")
            kwargs["stderr_path"].write_text("")
            (kwargs["cwd"] / "child.py").write_text("def f():\n    return 8\n")
            return CliRunResult(
                exit_code=0,
                stdout="",
                stderr="",
                wall_s=0.0,
                timed_out=False,
            )

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            backend = CliMutationBackend(
                kind="claude",
                binary="/usr/bin/claude",
                model="base-model",
            )
            request = MutationRequest(
                prompt={"system": "", "user": "brief"},
                parent_code=INITIAL_PROGRAM,
                work_dir=work,
                deliverable_path=work / "child.py",
                timeout_s=5.0,
                model="strong-model",
            )
            with patch.object(CliRunner, "run", fake_run):
                result = backend.run(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.backend_trace["model"], "strong-model")
        self.assertIn("strong-model", captured["argv"])
        self.assertNotIn("base-model", captured["argv"])

    def test_configured_cli_model_is_the_fallback(self):
        def fake_run(_self, argv, **kwargs):
            kwargs["stdout_path"].write_text("")
            kwargs["stderr_path"].write_text("")
            (kwargs["cwd"] / "child.py").write_text("def f():\n    return 8\n")
            return CliRunResult(
                exit_code=0,
                stdout="",
                stderr="",
                wall_s=0.0,
                timed_out=False,
            )

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            backend = CliMutationBackend(
                kind="opencode",
                binary="/usr/bin/opencode",
                model="base-model",
            )
            request = MutationRequest(
                prompt={"system": "", "user": "brief"},
                parent_code=INITIAL_PROGRAM,
                work_dir=work,
                deliverable_path=work / "child.py",
                timeout_s=5.0,
            )
            with patch.object(CliRunner, "run", fake_run):
                result = backend.run(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.backend_trace["model"], "base-model")


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

    def test_mutation_writes_deliverable_under_output_dir_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code="def f():\n    return 7\n")
            session, _ = make_session(tmp, mutation_backend=backend)

            asyncio.run(session.run_agent_mode())

            expected = Path(session.output_dir) / "mutations" / "it000000" / "m01" / "child.py"
            self.assertTrue(expected.is_file())
            self.assertIn("return 7", expected.read_text())

    def test_brief_is_host_assembled_prompt_not_a_path_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(code="def f():\n    return 7\n")
            session, _ = make_session(tmp, mutation_backend=backend)

            asyncio.run(session.run_agent_mode())

            brief = trace_rows(session)[0]["prompt"]["user"]
            # Default config is diff_based_evolution=True → SEARCH/REPLACE strategy.
            self.assertIn("<<<<<<< SEARCH", brief)
            self.assertNotIn("child.py", brief)
            self.assertNotIn("MUTATION_DELIVERABLE", brief)
            self.assertIn(RecordingCoordination.PROMPT_BLOCK, brief)


class TestAgentEscalation(unittest.TestCase):
    def test_random_escalation_reaches_mutation_request_model(self):
        requests = []
        config = make_config(
            max_iterations=1,
            coordination=CoordinationConfig(
                module="null",
                escalation=EscalationConfig(
                    trigger="random",
                    probability=1.0,
                    burst_length=1,
                    cooldown_mutations=0,
                    escalation_model="strong-model",
                ),
            ),
        )
        backend = FakeMutationBackend(
            producer=lambda request: (requests.append(request) or "def f():\n    return 7\n")
        )
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = make_session(
                tmp,
                mutation_backend=backend,
                coordination=NullCoordination(),
                config=config,
            )
            asyncio.run(session.run_agent_mode())

        self.assertEqual([request.model for request in requests], ["strong-model"])

    def test_random_escalation_sequence_is_seed_deterministic(self):
        def model_sequence():
            requests = []
            children = iter(range(2, 8))
            config = make_config(
                max_iterations=6,
                coordination=CoordinationConfig(
                    module="null",
                    escalation=EscalationConfig(
                        trigger="random",
                        probability=0.5,
                        burst_length=1,
                        cooldown_mutations=0,
                        escalation_model="strong-model",
                    ),
                ),
            )
            backend = FakeMutationBackend(
                producer=lambda request: (
                    requests.append(request) or f"def f():\n    return {next(children)}\n"
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                session, _ = make_session(
                    tmp,
                    mutation_backend=backend,
                    coordination=NullCoordination(),
                    stop_children=6,
                    config=config,
                )
                asyncio.run(session.run_agent_mode())
            return [request.model for request in requests]

        self.assertEqual(model_sequence(), model_sequence())

    def test_budget_fraction_warns_and_disables_escalation(self):
        config = make_config(
            coordination=CoordinationConfig(
                module="null",
                escalation=EscalationConfig(
                    trigger="budget_fraction",
                    escalation_model="strong-model",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp, warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            session, _ = make_session(
                tmp,
                mutation_backend=FakeMutationBackend(code="def f():\n    return 7\n"),
                coordination=NullCoordination(),
                config=config,
            )

        self.assertIsNone(session.escalation)
        self.assertTrue(any("budget_fraction escalation" in str(item.message) for item in seen))


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

            asyncio.run(session.run_agent_mode())

            child = child_of(session)
            self.assertAlmostEqual(child.metrics["combined_score"], 0.7)
            self.assertIn("return 7", child.code)
            self.assertNotIn("SEARCH", child.code)


class TestAttemptTraceAndArtifacts(unittest.TestCase):
    """A2: failing mutations leave attempt_trace rows and never touch the store."""

    def test_backend_failure_writes_trace_without_population_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeMutationBackend(fail_error="deliverable missing")
            session, _ = make_session(tmp, mutation_backend=backend)

            with self.assertRaises(RuntimeError):
                asyncio.run(session.run_agent_mode())

            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            rows = trace_rows(session)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "provider_failure")
            self.assertIn("deliverable missing", rows[0]["error"])

    def test_unparseable_deliverable_writes_trace_without_population_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Diff mode: a whitespace-only deliverable materializes to nothing.
            backend = FakeMutationBackend(code="   \n")
            session, _ = make_session(tmp, mutation_backend=backend)

            with self.assertRaises(RuntimeError):
                asyncio.run(session.run_agent_mode())

            self.assertEqual([p.id for p in session.store.population()], ["initial"])
            rows = trace_rows(session)
            self.assertEqual(rows[0]["outcome"], "provider_failure")
            self.assertIn("no parseable code", rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
