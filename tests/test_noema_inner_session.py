"""Inner-session read snapshot + ADVICE deliverable parsers (0175) and inner MCP (0179)."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openevolve.config import DatabaseConfig
from openevolve.database import Program

from noema.agenthost.config import (
    AgentCliConfig,
    AgentConfig,
)
from noema.agenthost.factory import create_agent_session
from noema.agenthost.inner_session_mcp import (
    MCP_CONFIG_NAME,
    build_server,
    get_best_programs,
    get_children_by_parent,
    get_coordination_snapshot,
    get_memory_status,
    get_parents_by_child,
    get_program,
    prepare_inner_mcp,
    submit_coordination,
    submit_mutation,
)
from noema.agenthost.mutation import CliMutationBackend, MutationRequest
from noema.agenthost.read_tools import build_snapshot
from noema.agenthost.submit import ADVICE_FILENAME, coordination_response
from noema.budget.cli_runner import CliRunner, CliRunResult, build_mutation_cli_command
from noema.config import CoordinationConfig, LLMClientConfig, LLMRolesConfig, NoemaConfig
from noema.substrates.cvt import CVTStore
from noema.substrates.flat import FlatPopulationStore
from noema.substrates.islands import IslandsStore
from noema.substrates.tree import TreeStore
from tests.test_noema_agent_arm_sweep import EVAL_SCRIPT, INITIAL_PROGRAM


def _deep_session(tmp: str, *, coordination_cli: AgentCliConfig | None = None):
    eval_path = os.path.join(tmp, "evaluator.py")
    with open(eval_path, "w") as handle:
        handle.write(EVAL_SCRIPT)
    agent_cfg = AgentConfig(
        noema=NoemaConfig(
            coordination=CoordinationConfig(module="hifo"),
            llm=LLMRolesConfig(
                coordination=LLMClientConfig(api_key="fake-key"),
            ),
        ),
        coordination_depth="deep",
        coordination_cli=coordination_cli or AgentCliConfig(kind="opencode"),
    )
    return create_agent_session(
        agent_cfg,
        evaluation_file=eval_path,
        initial_program_code=INITIAL_PROGRAM,
        output_dir=os.path.join(tmp, "out"),
    )


class TestInnerSession(unittest.TestCase):
    def _session(self, tmp: str):
        return _deep_session(tmp)

    def test_build_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp)
            asyncio.run(session.begin_run())
            snapshot = build_snapshot(session)
            self.assertIn("programs", snapshot)
            self.assertIn("coordination", snapshot)
            self.assertNotIn("memory_status", snapshot)
            self.assertGreaterEqual(len(snapshot["programs"]), 1)
            self.assertIn("initial", snapshot["programs"])
            json.dumps(snapshot)

    def test_module_help_is_warning_clean(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-W",
                "error",
                "-m",
                "noema.agenthost.inner_session_mcp",
                "--help",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("RuntimeWarning", completed.stderr)

    def test_build_snapshot_reuses_store_population_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp)
            asyncio.run(session.begin_run())
            snapshot = build_snapshot(session)
            store_snap = session.store.snapshot(None, limit=None)
            population = snapshot["population"]
            self.assertEqual(population["topology"], store_snap.topology)
            self.assertEqual(
                [row["id"] for row in population["top_programs"]],
                [view.id for view in store_snap.top_programs],
            )
            self.assertEqual(list(population["fitnesses"]), list(store_snap.fitnesses))
            self.assertEqual(len(population["regions"]), len(store_snap.regions))

    def test_build_snapshot_lineage_map_uses_program_parent_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self._session(tmp)
            asyncio.run(session.begin_run())
            child = Program(
                id="child",
                code=INITIAL_PROGRAM.replace("return 1", "return 2"),
                language=session.config.language,
                parent_id="initial",
                metrics={"combined_score": 0.2},
                iteration_found=1,
            )
            session.store.add(child, iteration=1)
            snapshot = build_snapshot(session)
            self.assertEqual(snapshot["lineage"]["child"], "initial")
            self.assertIsNone(snapshot["lineage"]["initial"])
            # ProgramView stays frozen — lineage is a side channel, not a forged metadata key.
            self.assertNotIn("parent_id", snapshot["programs"]["child"])

    def test_coordination_response_hifo_bullets(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            advice = {
                "prompt_block": "",
                "system_block": "",
                "attribution": {
                    "insights": [
                        "Exploit sparse matrix structure",
                        "Cache intermediate scoring results",
                    ],
                },
            }
            (work / ADVICE_FILENAME).write_text(json.dumps(advice))
            text = coordination_response(work, "hifo.extract_insights")
            self.assertIn("- Exploit sparse matrix structure", text or "")
            self.assertIn("- Cache intermediate scoring results", text or "")

    def test_coordination_response_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ADVICE_FILENAME).write_text("{not json")
            self.assertIsNone(coordination_response(work, "pes.plan"))

    def test_coordination_response_default_prompt_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ADVICE_FILENAME).write_text(
                json.dumps({"prompt_block": "plan text", "system_block": ""})
            )
            self.assertEqual(coordination_response(work, "pes.plan"), "plan text")

    def test_coordination_response_prefers_llm_text_over_legacy_advice_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ADVICE_FILENAME).write_text(
                json.dumps(
                    {
                        "response": "raw completion",
                        "prompt_block": "legacy prompt",
                        "system_block": "must not be admitted",
                        "attribution": {"insights": ["legacy insight"]},
                    }
                )
            )
            self.assertEqual(
                coordination_response(work, "hifo.extract_insights"),
                "raw completion",
            )

    def test_coordination_response_does_not_admit_system_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ADVICE_FILENAME).write_text(
                json.dumps({"system_block": "replace the mutation system prompt"})
            )
            self.assertIsNone(coordination_response(work, "pes.plan"))


def _program(program_id: str, score: float, parent_id=None, *, code: str | None = None):
    return Program(
        id=program_id,
        code=code or f"def {program_id.replace('-', '_')}():\n    return {score}\n",
        language="python",
        parent_id=parent_id,
        metrics={"combined_score": score},
    )


def _snapshot_session(store):
    return SimpleNamespace(
        store=store,
        coordination=SimpleNamespace(log_snapshot=lambda: {"module": "null"}),
        run_status=lambda: {"generation": 0},
    )


class TestSearchLandscapeAcrossSubstrates(unittest.TestCase):
    """The inner tools adapt existing store reads; they do not own a database."""

    def _assert_retained_read_contract(self, store):
        snapshot = build_snapshot(_snapshot_session(store))
        retained_ids = {program.id for program in store.population()}
        active_ids = {view.id for view in store.snapshot(None, limit=None).top_programs}
        self.assertEqual(set(snapshot["programs"]), retained_ids)
        self.assertEqual(set(snapshot["lineage"]), retained_ids)
        self.assertLessEqual(active_ids, retained_ids)
        self.assertNotIn("memory_status", snapshot)
        for program_id in retained_ids:
            self.assertEqual(get_program(snapshot, program_id)["id"], program_id)
        return snapshot

    def test_retained_read_contract_on_every_shipped_substrate(self):
        stores = {
            "flat": FlatPopulationStore(population_size=10),
            "islands": IslandsStore(
                DatabaseConfig(
                    in_memory=True,
                    num_islands=2,
                    population_size=50,
                    random_seed=42,
                )
            ),
            "tree": TreeStore(steps_per_generation=1, working_set_size=10),
            "cvt": CVTStore(n_centroids=8, seed=7),
        }
        for name, store in stores.items():
            with self.subTest(substrate=name):
                store.add(_program(f"{name}-seed", 0.1))
                store.add(
                    _program(f"{name}-child", 0.2, f"{name}-seed"),
                    iteration=1,
                )
                self._assert_retained_read_contract(store)

    def test_tree_active_view_can_be_smaller_but_full_lineage_resolves(self):
        store = TreeStore(steps_per_generation=1, working_set_size=1)
        store.add(_program("seed", 0.1))
        store.add(_program("child", 0.2, "seed"))
        store.add(_program("grandchild", 0.3, "child"))

        snapshot = self._assert_retained_read_contract(store)
        self.assertEqual(
            [row["id"] for row in snapshot["population"]["top_programs"]],
            ["grandchild"],
        )
        self.assertEqual(set(snapshot["programs"]), {"seed", "child", "grandchild"})
        parents = get_parents_by_child(snapshot, "grandchild", limit=5)
        self.assertEqual([row["id"] for row in parents["programs"]], ["child", "seed"])

    def test_cvt_displaced_elite_remains_readable_for_lineage(self):
        store = CVTStore(n_centroids=8, seed=7)
        same_cell_code = "def solve():\n    return sum(range(5))\n"
        store.add(_program("parent", 0.1, code=same_cell_code))
        store.add(_program("child", 0.9, "parent", code=same_cell_code))

        snapshot = self._assert_retained_read_contract(store)
        self.assertEqual(
            [row["id"] for row in snapshot["population"]["top_programs"]],
            ["child"],
        )
        self.assertEqual(set(snapshot["programs"]), {"parent", "child"})
        parents = get_parents_by_child(snapshot, "child", limit=5)
        self.assertEqual([row["id"] for row in parents["programs"]], ["parent"])

    def test_flat_lineage_stops_at_the_store_retention_boundary(self):
        store = FlatPopulationStore(population_size=2)
        store.add(_program("evicted-seed", 0.1))
        store.add(_program("parent", 0.2, "evicted-seed"))
        store.add(_program("child", 0.3, "parent"))

        snapshot = self._assert_retained_read_contract(store)
        self.assertEqual(set(snapshot["programs"]), {"parent", "child"})
        self.assertEqual(snapshot["lineage"]["parent"], "evicted-seed")
        self.assertIn("error", get_program(snapshot, "evicted-seed"))
        parents = get_parents_by_child(snapshot, "child", limit=5)
        self.assertEqual([row["id"] for row in parents["programs"]], ["parent"])


def _fixture_snapshot() -> dict:
    def program(pid, fitness):
        return {
            "id": pid,
            "fitness": fitness,
            "code": f"# {pid}\n",
            "changes_description": f"changed {pid}",
            "metrics": {"combined_score": fitness},
            "generation": 0,
            "iteration_found": 0,
            "metadata": {},
        }

    programs = {
        "initial": program("initial", 0.1),
        "c1": program("c1", 0.5),
        "c2": program("c2", 0.9),
        "c3": program("c3", 0.3),
    }
    ranked = sorted(programs.values(), key=lambda row: row["fitness"], reverse=True)
    regions = [{"scope": 0, "label": "island_0", "best_fitness": 0.9, "size": 4}]
    return {
        "run_status": {"children_accepted": 2, "stop_children": 5, "generation": 1},
        "coordination": {"module": "hifo", "tips": ["cache results"]},
        "population": {
            "scope": None,
            "topology": "unstructured",
            "fitnesses": [0.9, 0.5, 0.3, 0.1],
            "regions": regions,
            "best_program": ranked[0],
            "top_programs": ranked,
        },
        "lineage": {
            "initial": None,
            "c1": "initial",
            "c2": "c1",
            "c3": "c1",
        },
        "programs": programs,
    }


class TestInnerMcpReadTools(unittest.TestCase):
    """Six LoongFlow-shaped read wrappers over the host snapshot (0179)."""

    def setUp(self):
        self.snapshot = _fixture_snapshot()

    def test_memory_status_reports_counts_and_run_status(self):
        status = get_memory_status(self.snapshot)
        self.assertEqual(status["num_programs"], 4)
        self.assertEqual(status["best_fitness"], 0.9)
        self.assertEqual(status["run_status"]["children_accepted"], 2)
        self.assertEqual(len(status["regions"]), 1)
        self.assertEqual(status["topology"], "unstructured")

    def test_best_programs_ranked_by_fitness_and_limited(self):
        best = get_best_programs(self.snapshot, limit=2)
        self.assertEqual(best["count"], 2)
        self.assertEqual([item["id"] for item in best["programs"]], ["c2", "c1"])

    def test_program_by_id_and_missing(self):
        self.assertEqual(get_program(self.snapshot, "c1")["fitness"], 0.5)
        self.assertIn("error", get_program(self.snapshot, "nope"))

    def test_parents_by_child_walks_the_chain(self):
        parents = get_parents_by_child(self.snapshot, "c2", limit=5)
        self.assertEqual(parents["count"], 2)
        self.assertEqual([item["id"] for item in parents["programs"]], ["c1", "initial"])

    def test_parents_by_child_respects_limit(self):
        parents = get_parents_by_child(self.snapshot, "c2", limit=1)
        self.assertEqual([item["id"] for item in parents["programs"]], ["c1"])

    def test_children_by_parent(self):
        children = get_children_by_parent(self.snapshot, "c1", limit=5)
        self.assertEqual(sorted(item["id"] for item in children["programs"]), ["c2", "c3"])

    def test_coordination_snapshot_passthrough(self):
        self.assertEqual(get_coordination_snapshot(self.snapshot)["module"], "hifo")


class TestInnerMcpSubmit(unittest.TestCase):
    """Submit tools finalize the host-owned deliverable; the host still admits (0179)."""

    def test_submit_mutation_writes_deliverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            deliverable = Path(tmp) / "child.py"
            result = submit_mutation(deliverable, "def f():\n    return 2\n")
            self.assertEqual(deliverable.read_text(), "def f():\n    return 2\n")
            self.assertEqual(result["status"], "submitted")

    def test_submit_mutation_rejects_empty_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            deliverable = Path(tmp) / "child.py"
            self.assertIn("error", submit_mutation(deliverable, "   "))
            self.assertFalse(deliverable.exists())

    def test_submit_coordination_writes_llm_text_the_module_can_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            result = submit_coordination(work / ADVICE_FILENAME, "- reuse partial sums")
            self.assertEqual(result["status"], "submitted")
            self.assertEqual(
                json.loads((work / ADVICE_FILENAME).read_text()),
                {"response": "- reuse partial sums"},
            )
            self.assertEqual(
                coordination_response(work, "hifo.extract_insights"),
                "- reuse partial sums",
            )

    def test_submit_coordination_rejects_empty_or_structured_advice(self):
        with tempfile.TemporaryDirectory() as tmp:
            deliverable = Path(tmp) / ADVICE_FILENAME
            self.assertIn("error", submit_coordination(deliverable, "   "))
            self.assertIn(
                "error",
                submit_coordination(deliverable, {"prompt_block": "not admitted"}),
            )
            self.assertFalse(deliverable.exists())


class TestInnerMcpServer(unittest.TestCase):
    """The stdio server registers the six read tools plus the mode's submit tool."""

    def _tool_names(self, mode: str) -> set:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(_fixture_snapshot()))
            server = build_server(snapshot, mode, Path(tmp) / "out")
            return {tool.name for tool in asyncio.run(server.list_tools())}

    def test_mutation_mode_tools(self):
        names = self._tool_names("mutation")
        self.assertEqual(
            names,
            {
                "get_memory_status",
                "get_best_programs",
                "get_program",
                "get_parents_by_child",
                "get_children_by_parent",
                "get_coordination_snapshot",
                "submit_mutation",
            },
        )

    def test_coordination_mode_swaps_the_submit_tool(self):
        mutation = self._tool_names("mutation")
        coordination = self._tool_names("coordination")
        shared_reads = {
            "get_memory_status",
            "get_best_programs",
            "get_program",
            "get_parents_by_child",
            "get_children_by_parent",
            "get_coordination_snapshot",
        }
        self.assertEqual(mutation, shared_reads | {"submit_mutation"})
        self.assertEqual(coordination, shared_reads | {"submit_coordination"})

    def test_coordination_submit_schema_accepts_only_response_text(self):
        async def _schema():
            with tempfile.TemporaryDirectory() as tmp:
                snapshot = Path(tmp) / "snapshot.json"
                snapshot.write_text(json.dumps(_fixture_snapshot()))
                server = build_server(snapshot, "coordination", Path(tmp) / "out")
                tool = next(
                    item for item in await server.list_tools() if item.name == "submit_coordination"
                )
                return tool.inputSchema

        schema = asyncio.run(_schema())
        self.assertEqual(schema["required"], ["response"])
        self.assertEqual(set(schema["properties"]), {"response"})
        self.assertEqual(schema["properties"]["response"]["type"], "string")

    def test_list_tools_return_one_content_block(self):
        """FastMCP expands bare lists into N blocks; tools must return one object."""

        async def _probe():
            with tempfile.TemporaryDirectory() as tmp:
                snapshot = Path(tmp) / "snapshot.json"
                snapshot.write_text(json.dumps(_fixture_snapshot()))
                server = build_server(snapshot, "coordination", Path(tmp) / "out")
                results = {}
                for name, args in (
                    ("get_best_programs", {"limit": 3}),
                    ("get_parents_by_child", {"child_id": "c2"}),
                    ("get_children_by_parent", {"parent_id": "missing"}),
                ):
                    blocks = await server.call_tool(name, args)
                    if isinstance(blocks, tuple):
                        blocks = blocks[0]
                    results[name] = len(blocks)
                return results

        counts = asyncio.run(_probe())
        self.assertEqual(counts["get_best_programs"], 1)
        self.assertEqual(counts["get_parents_by_child"], 1)
        self.assertEqual(counts["get_children_by_parent"], 1)  # empty list → still one object


class TestInnerMcpAttachment(unittest.TestCase):
    """The inner server is attached to both deep CLI spawns (0179)."""

    def test_prepare_writes_snapshot_backup_and_mcp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = _deep_session(tmp)
            asyncio.run(session.begin_run())
            work = Path(tmp) / "attempt"
            config_path = prepare_inner_mcp(
                work, session, mode="mutation", deliverable=work / "child.py"
            )

            self.assertEqual(config_path, work / "tools" / MCP_CONFIG_NAME)
            self.assertTrue((work / "tools" / "snapshot.json").is_file())
            server = json.loads(config_path.read_text())["mcpServers"]["noema"]
            self.assertEqual(server["type"], "stdio")
            self.assertIn("noema.agenthost.inner_session_mcp", server["args"])
            self.assertIn("mutation", server["args"])

    def test_claude_argv_includes_mcp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            system = work / "SYSTEM.md"
            system.write_text("sys")
            config = work / "mcp.json"
            config.write_text("{}")
            cmd = build_mutation_cli_command(
                "claude",
                work_dir=work,
                system_path=system,
                user_message="mutate",
                binary="/usr/bin/claude",
                mcp_config_path=config,
            )
            self.assertIn("--mcp-config", cmd)
            self.assertIn(str(config), cmd)
            self.assertIn("--strict-mcp-config", cmd)
            self.assertEqual(cmd[-1], "mutate")

    def test_deep_coordination_spawn_attaches_inner_mcp(self):
        captures = {}
        current_kind = None

        def fake_run(_self, argv, **kwargs):
            captures[current_kind] = {
                "argv": argv,
                "cwd": kwargs["cwd"],
            }
            kwargs["stdout_path"].write_text("")
            kwargs["stderr_path"].write_text("")
            (kwargs["cwd"] / ADVICE_FILENAME).write_text(json.dumps({"response": "ok"}))
            return CliRunResult(exit_code=0, stdout="", stderr="", wall_s=0.0, timed_out=False)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(CliRunner, "run", fake_run):
                for kind in ("claude", "codex", "opencode", "agent"):
                    current_kind = kind
                    kind_tmp = Path(tmp) / kind
                    kind_tmp.mkdir()
                    session = _deep_session(
                        str(kind_tmp),
                        coordination_cli=AgentCliConfig(
                            kind=kind,
                            binary=f"/usr/bin/{kind}",
                        ),
                    )
                    asyncio.run(session.begin_run())
                    text = asyncio.run(session.coordination.llm.generate("brief", tag="pes.plan"))
                    self.assertEqual(text, "ok")

            for kind in ("claude", "codex", "opencode", "agent"):
                with self.subTest(kind=kind):
                    captured = captures[kind]
                    work = captured["cwd"]
                    self.assertEqual((work / "BRIEF.md").read_text(), "brief")
                    self.assertIn("submit_coordination", (work / "SYSTEM.md").read_text())
                    self.assertTrue((work / "tools" / "snapshot.json").is_file())
                    self.assertTrue((work / "tools" / MCP_CONFIG_NAME).is_file())
                    if kind == "claude":
                        self.assertIn("--mcp-config", captured["argv"])
                    elif kind == "codex":
                        self.assertIn(
                            "mcp_servers.noema.required=true",
                            captured["argv"],
                        )
                    elif kind == "opencode":
                        opencode = json.loads((work / "opencode.json").read_text())
                        self.assertTrue(opencode["mcp"]["noema"]["enabled"])
                    else:
                        self.assertTrue((work / ".cursor" / "mcp.json").is_file())
                        self.assertIn("--approve-mcps", captured["argv"])

    def test_cli_mutation_backend_attaches_inner_mcp_when_session_bound(self):
        captures = {}

        def fake_run(_self, argv, **kwargs):
            captures[kwargs["cwd"].parent.name] = {
                "argv": argv,
                "cwd": kwargs["cwd"],
            }
            kwargs["stdout_path"].write_text("")
            kwargs["stderr_path"].write_text("")
            (kwargs["cwd"] / "child.py").write_text("def f():\n    return 99\n")
            return CliRunResult(exit_code=0, stdout="", stderr="", wall_s=0.0, timed_out=False)

        with tempfile.TemporaryDirectory() as tmp:
            session = _deep_session(tmp)
            asyncio.run(session.begin_run())
            with patch.object(CliRunner, "run", fake_run):
                for kind in ("claude", "codex", "opencode", "agent"):
                    work = Path(tmp) / kind / "mut"
                    backend = CliMutationBackend(kind=kind, binary=f"/usr/bin/{kind}")
                    backend.bind_session(session)
                    request = MutationRequest(
                        prompt={"system": "sys", "user": "improve"},
                        parent_code=INITIAL_PROGRAM,
                        work_dir=work,
                        deliverable_path=work / "child.py",
                        timeout_s=5.0,
                    )
                    result = backend.run(request)
                    self.assertTrue(result.ok)

            for kind in ("claude", "codex", "opencode", "agent"):
                with self.subTest(kind=kind):
                    captured = captures[kind]
                    work = captured["cwd"]
                    self.assertTrue((work / "tools" / "snapshot.json").is_file())
                    self.assertTrue((work / "tools" / MCP_CONFIG_NAME).is_file())
                    if kind == "claude":
                        self.assertIn("--mcp-config", captured["argv"])
                    elif kind == "codex":
                        self.assertIn(
                            "mcp_servers.noema.required=true",
                            captured["argv"],
                        )
                    elif kind == "opencode":
                        opencode = json.loads((work / "opencode.json").read_text())
                        self.assertTrue(opencode["mcp"]["noema"]["enabled"])
                    else:
                        self.assertTrue((work / ".cursor" / "mcp.json").is_file())
                        self.assertIn("--approve-mcps", captured["argv"])

    def test_cli_mutation_backend_without_session_writes_no_mcp_config(self):
        def fake_run(_self, argv, **kwargs):
            kwargs["stdout_path"].write_text("")
            kwargs["stderr_path"].write_text("")
            (kwargs["cwd"] / "child.py").write_text("def f():\n    return 99\n")
            return CliRunResult(exit_code=0, stdout="", stderr="", wall_s=0.0, timed_out=False)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "mut"
            backend = CliMutationBackend(kind="claude", binary="/usr/bin/claude")
            request = MutationRequest(
                prompt={"system": "sys", "user": "improve"},
                parent_code=INITIAL_PROGRAM,
                work_dir=work,
                deliverable_path=work / "child.py",
                timeout_s=5.0,
            )
            with patch.object(CliRunner, "run", fake_run):
                backend.run(request)

            self.assertFalse((work / "tools" / MCP_CONFIG_NAME).exists())
