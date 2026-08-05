"""0188 Stage 1: instrumentation test (canonical method note §3, hard rule 3).

Proves the strict adapter serves the donor surface from Noema's wrapper and
never from the raw upstream database:

1. Static guarantee — the adapter's source contains no ``_db`` reference, so
   no method body can reach past ``IslandsStore``. (The spec's poisoned-``_db``
   sentinel is NOT used: ``IslandsStore``'s own methods legitimately touch
   ``self._db`` — that is the wrapper doing its job — so a poison pill one
   layer down would fail correct routing too. The static scan closes the hole
   the poison was aimed at: an adapter method implemented as
   ``self._store._db.X``. Deviation recorded in the Stage 1 log.)
2. Dynamic guarantee — every servable donor-surface item, when exercised, is
   observed as a call on an ``IslandsStore`` public method (call-recording via
   wrapped methods), and every unservable read AND write raises immediately.
"""

import inspect
import tempfile
import unittest
from unittest import mock

from openevolve.config import DatabaseConfig
from openevolve.database import Program

import tests.adapter_islands_store as adapter_module
from noema.substrates.islands import IslandsStore
from tests.adapter_islands_store import AdapterProgramDatabase


def _config(**overrides) -> DatabaseConfig:
    defaults = dict(in_memory=True, num_islands=3)
    defaults.update(overrides)
    return DatabaseConfig(**defaults)


def _program(pid: str, score: float) -> Program:
    return Program(id=pid, code=f"def f_{pid}(): pass", metrics={"combined_score": score})


class TestAdapterNeverTouchesRawDatabase(unittest.TestCase):
    def test_adapter_source_has_no_db_attribute_access(self):
        # AST-level: no expression anywhere in the adapter module accesses an
        # attribute named `_db` (docstrings mentioning it are fine).
        import ast

        tree = ast.parse(inspect.getsource(adapter_module))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "_db"
        ]
        self.assertEqual(
            offenders, [],
            f"AdapterProgramDatabase accesses ._db at lines {offenders} — hard rule 1 "
            "violation: every donor call must be served by IslandsStore's public API",
        )


class TestServableSurfaceRoutesThroughStore(unittest.TestCase):
    """Each servable donor-surface item is observed as an IslandsStore call."""

    # donor surface item -> IslandsStore/SubstrateDatabase public method serving it
    SERVING_CALLS = {
        "add": "add",
        "get": "get",
        "get_best_program": "best_program",
        "get_top_programs": "top_programs",
        "sample_from_island": "sample_from_island",
        "save": "save",
        "load": "load",
    }

    def test_every_servable_item_served_by_recorded_store_call(self):
        recorded = set()
        originals = {
            name: getattr(IslandsStore, serving)
            for name, serving in self.SERVING_CALLS.items()
        }

        def _recording(serving_name, original):
            def wrapper(self, *args, **kwargs):
                recorded.add(serving_name)
                return original(self, *args, **kwargs)
            return wrapper

        with unittest.mock.patch.object(
            IslandsStore, "add", _recording("add", originals["add"])
        ), mock.patch.object(
            IslandsStore, "get", _recording("get", originals["get"])
        ), mock.patch.object(
            IslandsStore, "best_program", _recording("best_program", originals["get_best_program"])
        ), mock.patch.object(
            IslandsStore, "top_programs", _recording("top_programs", originals["get_top_programs"])
        ), mock.patch.object(
            IslandsStore,
            "sample_from_island",
            _recording("sample_from_island", originals["sample_from_island"]),
        ), mock.patch.object(
            IslandsStore, "save", _recording("save", originals["save"])
        ), mock.patch.object(
            IslandsStore, "load", _recording("load", originals["load"])
        ):
            db = AdapterProgramDatabase(_config())
            db.add(_program("p1", 0.5), target_island=0)
            db.add(_program("p2", 0.9), target_island=2)
            db.get("p1")
            db.get_best_program()
            db.get_top_programs(2, island_idx=2)
            db.sample_from_island(0, num_inspirations=1)
            with tempfile.TemporaryDirectory() as tmp:
                db.save(tmp)
                db.load(tmp)
            self.assertIs(db.config, db._store.config)

        self.assertEqual(
            recorded,
            set(self.SERVING_CALLS.values()),
            "some servable donor-surface item was NOT served by its IslandsStore call",
        )


class TestUnservedSurfaceFailsLoudly(unittest.TestCase):
    UNSERVED_READS = [
        "current_island", "island_generations", "archive", "feature_bins",
        "island_best_programs", "island_feature_maps", "last_migration_generation",
        "programs", "islands", "best_program_id", "set_current_island",
        "log_island_status", "migrate_programs", "should_migrate", "sample",
        "_sample_inspirations", "_validate_migration_results",
        "_calculate_feature_coords", "_fast_code_diversity",
    ]

    def test_unserved_reads_raise_naming_the_capability(self):
        db = AdapterProgramDatabase(_config())
        for name in self.UNSERVED_READS:
            with self.assertRaises(NotImplementedError, msg=name) as ctx:
                getattr(db, name)
            self.assertIn(name, str(ctx.exception))

    def test_unserved_writes_raise_instead_of_silently_absorbing(self):
        db = AdapterProgramDatabase(_config())
        with self.assertRaises(NotImplementedError):
            db.current_island = 2
        with self.assertRaises(NotImplementedError):
            db.best_program_id = "x"

    def test_metric_kwarg_raises_on_both_query_methods(self):
        db = AdapterProgramDatabase(_config())
        db.add(_program("p1", 0.5))
        with self.assertRaises(NotImplementedError):
            db.get_best_program(metric="accuracy")
        with self.assertRaises(NotImplementedError):
            db.get_top_programs(2, metric="accuracy")


if __name__ == "__main__":
    unittest.main()
