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
        "island_feature_maps", "last_migration_generation",
        "set_current_island",
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

    def test_islands_and_programs_are_derived_readonly_views(self):
        # Served per the canonical note's own routing table:
        # db.islands[i] == ids of store.population(i); db.programs from
        # population(None). Reads work; mutations raise instead of being
        # absorbed by the throwaway copy — an absorbed mutation resurfaces
        # later as an ASSERTION mismatch, which the triage method reads as a
        # real-Noema-bug signal (donor test_island_best_with_missing_program
        # did exactly that before this was tightened).
        db = AdapterProgramDatabase(_config())
        db.add(_program("p1", 0.5), target_island=1)
        self.assertIn("p1", db.islands[1])
        self.assertEqual(set(db.programs), {"p1"})
        for mutate in (
            lambda: db.islands[1].discard("p1"),
            lambda: db.islands[1].remove("p1"),
            lambda: db.programs.pop("p1"),
            lambda: db.programs.__delitem__("p1"),
            lambda: db.programs.__setitem__("p2", _program("p2", 0.1)),
        ):
            with self.assertRaises(NotImplementedError):
                mutate()
        self.assertIn("p1", db.islands[1])
        self.assertEqual(set(db.programs), {"p1"})

    def test_unserved_deletes_raise_instead_of_bare_attributeerror(self):
        # mock.patch.object teardown calls delattr; a bare AttributeError there
        # hides which donor capability was actually missing.
        db = AdapterProgramDatabase(_config())
        with self.assertRaises(NotImplementedError) as ctx:
            del db.sample_from_island
        self.assertIn("sample_from_island", str(ctx.exception))

    def test_metric_kwarg_raises_on_both_query_methods(self):
        db = AdapterProgramDatabase(_config())
        db.add(_program("p1", 0.5))
        with self.assertRaises(NotImplementedError):
            db.get_best_program(metric="accuracy")
        with self.assertRaises(NotImplementedError):
            db.get_top_programs(2, metric="accuracy")


class TestTriageLedgerParity(unittest.TestCase):
    """Holds the Stage 1 triage ledger to the observed reality.

    Runs the whole adapter-routed donor suite and asserts the failing set is
    EXACTLY ``DECLARED_DEVIATIONS``, each failure naming its ledgered
    capability. This is what stops a ledger row from rotting: if Noema grows
    one of the deviating capabilities the donor test starts passing and this
    fires; if a donor test starts failing for a NEW reason (an assertion
    mismatch on the servable surface -- the (a) real-Noema-bug signal) this
    fires too.
    """

    def test_failing_donor_set_is_exactly_the_declared_deviations(self):
        import random

        import tests.test_noema_islands_adapter_fidelity_spec as spec

        state = random.getstate()
        self.addCleanup(random.setstate, state)
        observed = spec.run_donor_suite()

        self.assertEqual(
            set(observed),
            set(spec.DECLARED_DEVIATIONS),
            "adapter-routed donor failures drifted from the Stage 1 triage ledger "
            f"(newly failing: {sorted(set(observed) - set(spec.DECLARED_DEVIATIONS))}; "
            f"no longer failing: {sorted(set(spec.DECLARED_DEVIATIONS) - set(observed))})",
        )
        for key, capability in spec.DECLARED_DEVIATIONS.items():
            self.assertIn(
                capability,
                observed[key],
                f"{key} no longer fails on {capability!r} -- reclassify it in the "
                "Stage 1 triage ledger",
            )


if __name__ == "__main__":
    unittest.main()
