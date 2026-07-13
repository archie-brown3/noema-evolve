"""
Tests for noema adapters (database, evaluator, views)
"""

import asyncio
import os
import tempfile
import unittest
import uuid

from openevolve.config import DatabaseConfig, EvaluatorConfig
from openevolve.database import Program

from noema.database import SubstrateDatabase
from noema.evaluator import make_evaluator
from noema.views import ProgramView


def make_db(**overrides) -> SubstrateDatabase:
    defaults = dict(
        in_memory=True,
        num_islands=2,
        population_size=50,
        feature_dimensions=["complexity", "diversity"],
        random_seed=42,
        log_prompts=True,
        migration_interval=100,
    )
    defaults.update(overrides)
    return SubstrateDatabase(DatabaseConfig(**defaults))


def make_program(code="def f():\n    return 1\n", score=0.5, **kwargs) -> Program:
    return Program(
        id=kwargs.pop("id", str(uuid.uuid4())),
        code=code,
        language="python",
        metrics={"combined_score": score},
        **kwargs,
    )


class TestSubstrateDatabase(unittest.TestCase):
    def test_novelty_features_rejected(self):
        with self.assertRaises(ValueError):
            SubstrateDatabase(DatabaseConfig(embedding_model="text-embedding-3-small"))

    def test_add_and_sample_round_trip(self):
        db = make_db()
        program = make_program(score=0.7)
        db.add(program, iteration=1)

        parent, inspirations = db.sample_from_island(0, num_inspirations=3)
        self.assertEqual(parent.id, program.id)
        self.assertEqual(inspirations, [])
        self.assertEqual(db.num_programs, 1)

    def test_fitness_uses_combined_score(self):
        db = make_db()
        program = make_program(score=0.9)
        self.assertAlmostEqual(db.fitness(program), 0.9)

    def test_island_fitnesses(self):
        db = make_db(num_islands=1)
        # Codes of very different lengths so each lands in its own MAP-Elites
        # complexity cell (same-cell programs replace each other)
        for i, score in enumerate((0.2, 0.5, 0.8)):
            padding = "\n".join(f"    x{j} = {j}" for j in range(i * 20))
            db.add(make_program(code=f"def f():\n{padding}\n    return {score}\n", score=score))
        fitnesses = db.island_fitnesses(0)
        self.assertEqual(sorted(fitnesses), [0.2, 0.5, 0.8])

    def test_top_programs_ordering(self):
        db = make_db(num_islands=1)
        for score in (0.2, 0.9, 0.5):
            db.add(make_program(code=f"def f():\n    return {score}\n", score=score))
        top = db.top_programs(2, island=0)
        self.assertEqual([p.metrics["combined_score"] for p in top], [0.9, 0.5])

    def test_end_generation_advances_and_migrates_when_due(self):
        db = make_db(num_islands=2, migration_interval=1, migration_rate=0.5)
        # Give both islands a program so migration has material
        p0 = make_program(code="def a():\n    return 0\n", score=0.4)
        db.add(p0, iteration=0)
        db._db.set_current_island(1)
        p1 = make_program(code="def b():\n    return 1\n", score=0.6)
        db.add(p1, iteration=0)

        migrated = db.end_generation()
        self.assertTrue(migrated)

    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db()
            program = make_program(score=0.7)
            db.add(program, iteration=5)
            db.save(tmp, iteration=5)

            db2 = make_db()
            db2.load(tmp)
            self.assertEqual(db2.num_programs, 1)
            self.assertEqual(db2.last_iteration, 5)
            loaded = db2.get(program.id)
            self.assertEqual(loaded.code, program.code)


class TestProgramView(unittest.TestCase):
    def test_from_program_snapshot(self):
        program = make_program(score=0.7, id="p1")
        program.metadata["island"] = 1
        view = ProgramView.from_program(program, ["complexity", "diversity"])
        self.assertEqual(view.id, "p1")
        self.assertAlmostEqual(view.fitness, 0.7)
        self.assertEqual(view.metadata["island"], 1)

    def test_view_is_frozen_and_detached(self):
        program = make_program(score=0.7)
        view = ProgramView.from_program(program, [])
        with self.assertRaises(Exception):
            view.fitness = 1.0
        # Mutating the view's dicts must not touch the source program
        view.metrics["combined_score"] = 0.0
        self.assertEqual(program.metrics["combined_score"], 0.7)


class TestMakeEvaluator(unittest.TestCase):
    def _write_eval_file(self, tmp) -> str:
        path = os.path.join(tmp, "evaluator.py")
        with open(path, "w") as f:
            f.write(
                "def evaluate(program_path):\n"
                "    with open(program_path) as fh:\n"
                "        code = fh.read()\n"
                "    return {'combined_score': 0.5 if 'return' in code else 0.0}\n"
            )
        return path

    def test_llm_feedback_rejected(self):
        with self.assertRaises(ValueError):
            make_evaluator(EvaluatorConfig(use_llm_feedback=True), "whatever.py")

    def test_evaluate_program_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            evaluator = make_evaluator(
                EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0),
                self._write_eval_file(tmp),
            )
            metrics = asyncio.run(evaluator.evaluate_program("def f():\n    return 1\n", "prog-1"))
            self.assertEqual(metrics["combined_score"], 0.5)

    def test_artifacts_popped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evaluator.py")
            with open(path, "w") as f:
                f.write(
                    "from openevolve.evaluation_result import EvaluationResult\n"
                    "def evaluate(program_path):\n"
                    "    return EvaluationResult(\n"
                    "        metrics={'combined_score': 1.0},\n"
                    "        artifacts={'note': 'hello'},\n"
                    "    )\n"
                )
            evaluator = make_evaluator(
                EvaluatorConfig(cascade_evaluation=False, timeout=30, max_retries=0), path
            )
            asyncio.run(evaluator.evaluate_program("def f():\n    return 1\n", "prog-2"))
            artifacts = evaluator.get_pending_artifacts("prog-2")
            self.assertEqual(artifacts, {"note": "hello"})
            # Popped: second read returns nothing
            self.assertIsNone(evaluator.get_pending_artifacts("prog-2"))


if __name__ == "__main__":
    unittest.main()
