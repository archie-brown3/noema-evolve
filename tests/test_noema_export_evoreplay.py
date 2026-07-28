import hashlib
import json
import os
import tempfile
import unittest

from openevolve.database import Program

from noema.substrates.cvt import CVTStore
from noema.export_evoreplay import export_run
from noema.substrates.tree import TreeStore


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f)


class TestEvoReplayExport(unittest.TestCase):
    def test_same_run_exports_byte_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "run")
            checkpoint = os.path.join(run, "checkpoints", "checkpoint_0", "programs")
            write_json(
                os.path.join(checkpoint, "initial.json"),
                {
                    "id": "initial",
                    "code": "def f():\n    return 1\n",
                    "language": "python",
                    "metrics": {"combined_score": 0.1},
                    "iteration_found": 0,
                    "parent_id": None,
                    "timestamp": 1.0,
                    "metadata": {},
                    "generation": 0,
                    "prompts": None,
                },
            )
            first = export_run(run, os.path.join(tmp, "first"))
            second = export_run(run, os.path.join(tmp, "second"))

            def contents(root):
                return {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }

            self.assertEqual(contents(first), contents(second))

    def test_exports_stock_refined_layout_losslessly(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "run-7")
            output = os.path.join(tmp, "refined")
            os.makedirs(run)
            with open(os.path.join(run, "config.yaml"), "w") as f:
                f.write(
                    "language: python\n"
                    "llm:\n"
                    "  mutation:\n"
                    "    api_key: sk-test-secret-that-must-not-export\n"
                    "    max_tokens: 1024\n"
                    "coordination:\n"
                    "  params:\n"
                    "    service_api_key: provider-secret-value\n"
                    "    client_secret: oauth-secret-value\n"
                    "    password: database-password-value\n"
                    "    token: generic-token-value\n"
                    "    service_token: service-token-value\n"
                    "    auth_token: auth-token-value\n"
                    "    secret_access_key: access-key-value\n"
                    "    private_key: private-key-value\n"
                )

            checkpoint = os.path.join(run, "checkpoints", "checkpoint_3")
            programs = os.path.join(checkpoint, "programs")
            os.makedirs(programs)
            initial = {
                "id": "initial",
                "code": "def f():\n    return 1\n",
                "language": "python",
                "metrics": {"combined_score": 0.1},
                "iteration_found": 0,
                "parent_id": None,
                "timestamp": 1.0,
                "metadata": {"island": 0},
                "generation": 0,
                "prompts": None,
            }
            child = {
                "id": "it000003",
                "code": "def f():\n    return 2\n",
                "language": "python",
                "metrics": {"combined_score": 0.2},
                "iteration_found": 3,
                "parent_id": "initial",
                "timestamp": 2.0,
                "metadata": {"source_attempt_id": "run-7:000003:00"},
                "generation": 1,
                "prompts": {"full_rewrite_user": {"user": "full prompt"}},
            }
            write_json(os.path.join(programs, "initial.json"), initial)
            write_json(os.path.join(programs, "it000003.json"), child)
            write_json(
                os.path.join(checkpoint, "metadata.json"),
                {
                    "islands": [["initial", "it000003"]],
                    "island_best_programs": ["it000003"],
                    "archive": ["initial", "it000003"],
                    "best_program_id": "it000003",
                    "last_iteration": 3,
                },
            )
            write_json(
                os.path.join(checkpoint, "noema_state.json"),
                {
                    "ledger": {
                        "records": [
                            {
                                "call_id": "call-000000",
                                "iteration": 3,
                                "account": "mutation",
                                "prompt_tokens": 10,
                                "completion_tokens": 5,
                                "cost": 0.01,
                            }
                        ]
                    }
                },
            )
            attempt = {
                "attempt_id": "run-7:000003:00",
                "iteration": 3,
                "outcome": "accepted",
            }
            with open(os.path.join(run, "attempt_trace.jsonl"), "w") as f:
                f.write(json.dumps(attempt) + "\n")
            selection = {
                "iteration": 3,
                "program_id": "it000003",
                "selected_attempt_id": "run-7:000003:00",
            }
            with open(os.path.join(run, "selection_trace.jsonl"), "w") as f:
                f.write(json.dumps(selection) + "\n")

            export_run(run, output)

            expected = {
                "meta.json",
                "run_config.yaml",
                "programs.jsonl",
                "iterations.jsonl",
                "iter_scalars.jsonl",
                "noema_attempts.jsonl",
                "noema_selections.jsonl",
                "blobs",
            }
            self.assertTrue(expected.issubset(set(os.listdir(output))))
            with open(os.path.join(output, "run_config.yaml")) as f:
                exported_config = f.read()
            self.assertNotIn("sk-test-secret", exported_config)
            self.assertNotIn("provider-secret-value", exported_config)
            self.assertNotIn("oauth-secret-value", exported_config)
            self.assertNotIn("database-password-value", exported_config)
            self.assertNotIn("generic-token-value", exported_config)
            self.assertNotIn("service-token-value", exported_config)
            self.assertNotIn("auth-token-value", exported_config)
            self.assertNotIn("access-key-value", exported_config)
            self.assertNotIn("private-key-value", exported_config)
            self.assertIn("<redacted>", exported_config)
            self.assertIn("max_tokens: 1024", exported_config)
            with open(os.path.join(output, "meta.json")) as f:
                self.assertEqual(json.load(f)["source"], "run-7")

            with open(os.path.join(output, "programs.jsonl")) as f:
                exported = {row["id"]: row for row in map(json.loads, f)}
            row = exported["it000003"]
            solution_sha = hashlib.sha256(child["code"].encode()).hexdigest()
            self.assertEqual(row["status"], "accepted")
            self.assertEqual(row["source"], "checkpoint.programs")
            self.assertEqual(row["solution_sha256"], solution_sha)
            self.assertNotIn("code", row)
            with open(os.path.join(output, "blobs", solution_sha[:2], f"{solution_sha}.txt")) as f:
                self.assertEqual(f.read(), child["code"])

            with open(os.path.join(output, "noema_attempts.jsonl")) as f:
                self.assertEqual(json.loads(f.readline()), attempt)
            with open(os.path.join(output, "noema_selections.jsonl")) as f:
                self.assertEqual(json.loads(f.readline()), selection)

            with open(os.path.join(output, "iter_scalars.jsonl")) as f:
                scalars = list(map(json.loads, f))
            self.assertIn({"iteration": 3, "key": "mutation_tokens", "value": 15}, scalars)

    def test_refuses_to_overwrite_an_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "run")
            output = os.path.join(tmp, "refined")
            os.makedirs(run)
            os.makedirs(output)
            with self.assertRaises(FileExistsError):
                export_run(run, output)

    def test_exports_trace_only_run_without_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "run")
            output = os.path.join(tmp, "refined")
            os.makedirs(run)
            with open(os.path.join(run, "config.yaml"), "w") as f:
                f.write("llm:\n  api_key: trace-only-secret\n")

            parent = {
                "id": "initial",
                "code": "def f():\n    return 1\n",
                "metrics": {"combined_score": 0.1},
                "iteration_found": 0,
                "parent_id": None,
                "metadata": {},
                "generation": 0,
            }
            attempt = {
                "attempt_id": "run:000001:00",
                "iteration": 1,
                "outcome": "accepted",
                "parent": parent,
                "candidate": {"id": "it000001", "code": "def f():\n    return 2\n"},
                "generation": 1,
                "timestamp": 2.0,
            }
            evolution = {
                "iteration": 1,
                "parent_id": "initial",
                "child_id": "it000001",
                "child_metrics": {"combined_score": 0.2},
                "generation": 1,
                "timestamp": 2.0,
            }
            selection = {
                "iteration": 1,
                "program_id": "it000001",
                "selected_attempt_id": "run:000001:00",
            }
            for filename, rows in (
                ("attempt_trace.jsonl", [attempt]),
                ("evolution_trace.jsonl", [evolution]),
                ("selection_trace.jsonl", [selection]),
            ):
                with open(os.path.join(run, filename), "w") as f:
                    for row in rows:
                        f.write(json.dumps(row) + "\n")

            export_run(run, output)

            with open(os.path.join(output, "programs.jsonl")) as f:
                programs = {row["id"]: row for row in map(json.loads, f)}
            self.assertEqual(set(programs), {"initial", "it000001"})
            self.assertEqual(programs["it000001"]["parent_id"], "initial")
            self.assertEqual(programs["it000001"]["metrics"], {"combined_score": 0.2})
            self.assertEqual(programs["it000001"]["source"], "attempt_trace.candidate")
            with open(os.path.join(output, "meta.json")) as f:
                self.assertEqual(json.load(f)["counts"], {
                    "checkpoints": 0,
                    "accepted_unique": 2,
                    "attempts": 1,
                })
            with open(os.path.join(output, "noema_attempts.jsonl")) as f:
                self.assertEqual(json.loads(f.readline()), attempt)
            with open(os.path.join(output, "noema_selections.jsonl")) as f:
                self.assertEqual(json.loads(f.readline()), selection)
            with open(os.path.join(output, "run_config.yaml")) as f:
                self.assertNotIn("trace-only-secret", f.read())

    def test_exports_cvt_and_tree_store_programs(self):
        for filename, store in (
            ("cvt_store.json", CVTStore(n_centroids=4, seed=3)),
            ("tree_store.json", TreeStore()),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                run = os.path.join(tmp, "run")
                checkpoint = os.path.join(run, "checkpoints", "checkpoint_0")
                store.add(
                    Program(
                        id="initial",
                        code="def f():\n    return 1\n",
                        language="python",
                        metrics={"combined_score": 0.1},
                        iteration_found=0,
                    )
                )
                store.save(checkpoint, iteration=0)
                output = os.path.join(tmp, "refined")

                export_run(run, output)

                with open(os.path.join(output, "programs.jsonl")) as f:
                    programs = list(map(json.loads, f))
                with open(os.path.join(output, "iterations.jsonl")) as f:
                    memberships = list(map(json.loads, f))
                self.assertEqual([row["id"] for row in programs], ["initial"])
                self.assertEqual(
                    programs[0]["source"],
                    f"{filename.removesuffix('.json')}.programs",
                )
                self.assertTrue(memberships)
