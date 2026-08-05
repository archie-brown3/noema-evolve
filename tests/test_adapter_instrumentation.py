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

0188 Stage 7 (wrapper widening) grew the servable half from 7 items to the whole
donor surface, so the two guarantees now carry much more weight: with nothing
left in the declared-deviation ledger, these are what stand between "Noema's
wrapper answers the donor suite" and "something else does".
"""

import contextlib
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
    """Each servable donor-surface item is observed as an IslandsStore access.

    0188 Stage 7 widened this from 7 items to the whole donor surface. The
    recording is deliberately per-WRAPPER-MEMBER rather than per-adapter-method:
    what needs proving is that Noema's wrapper was the thing that answered, so
    an adapter method that quietly grew its own implementation would show up
    here as a missing recording, not as a passing test.
    """

    # donor surface item -> the IslandsStore/SubstrateDatabase member serving it.
    # Several donor names differ from the wrapper name on purpose: upstream's
    # privates get public Noema names.
    SERVING_CALLS = {
        "add": "add",
        "get": "get",
        "get_best_program": "best_program",
        "get_top_programs": "top_programs",
        "sample": "sample",
        "sample_from_island": "sample_from_island",
        "save": "save",
        "load": "load",
        "set_current_island": "set_current_island",
        "next_island": "next_island",
        "should_migrate": "should_migrate",
        "migrate_programs": "migrate_programs",
        "log_island_status": "log_island_status",
        "store_artifacts": "store_artifacts",
        "get_artifacts": "get_artifacts",
        "_validate_migration_results": "validate_migration_results",
        "_calculate_feature_coords": "feature_coords",
        "_feature_coords_to_key": "feature_coords_to_key",
        "_calculate_complexity_bin": "complexity_bin",
        "_calculate_diversity_bin": "diversity_bin",
        "_fast_code_diversity": "code_diversity",
        "_sample_inspirations": "sample_inspirations",
        "programs": "programs",
        "islands": "islands",
        "archive": "archive",
        "feature_bins": "feature_bins",
        "island_feature_maps": "island_feature_maps",
        "island_generations": "island_generations",
        "island_best_programs": "island_best_programs",
        "last_migration_generation": "last_migration_generation",
        "best_program_id": "best_program_id",
        "current_island": "current_island",
    }

    @staticmethod
    def _recording(member, original, recorded):
        """Wrap a wrapper member so any use of it is observed.

        Properties need a property back — replacing one with a function would
        change how the adapter's own code reaches it, and then the test would be
        measuring the instrument.
        """
        if isinstance(original, property):
            def getter(self):
                recorded.add(member)
                return original.fget(self)

            setter = None
            if original.fset is not None:
                def setter(self, value):  # noqa: F811 — the property's setter half
                    recorded.add(f"{member}=")
                    original.fset(self, value)

            return property(getter, setter)

        def wrapper(self, *args, **kwargs):
            recorded.add(member)
            return original(self, *args, **kwargs)

        return wrapper

    def test_every_servable_item_served_by_recorded_store_call(self):
        recorded = set()
        with contextlib.ExitStack() as stack:
            for member in set(self.SERVING_CALLS.values()):
                stack.enter_context(
                    mock.patch.object(
                        IslandsStore,
                        member,
                        self._recording(member, getattr(IslandsStore, member), recorded),
                    )
                )

            db = AdapterProgramDatabase(_config())
            db.add(_program("p1", 0.5), target_island=0)
            db.add(_program("p2", 0.9), target_island=2)
            db.get("p1")
            db.get_best_program()
            db.get_top_programs(2, island_idx=2)
            db.sample(num_inspirations=1)
            db.sample_from_island(0, num_inspirations=1)
            db.set_current_island(1)
            db.next_island()
            db.should_migrate()
            db.migrate_programs()
            db.log_island_status()
            db.store_artifacts("p1", {"stderr": "boom"})
            db.get_artifacts("p1")
            db._validate_migration_results()
            coords = db._calculate_feature_coords(db.get("p1"))
            db._feature_coords_to_key(coords)
            db._calculate_complexity_bin(10)
            db._calculate_diversity_bin(0.5)
            db._fast_code_diversity("a", "b")
            db._sample_inspirations(db.get("p1"), n=1)
            for name in (
                "programs", "islands", "archive", "feature_bins",
                "island_feature_maps", "island_generations",
                "island_best_programs", "last_migration_generation",
                "best_program_id", "current_island",
            ):
                getattr(db, name)
            with tempfile.TemporaryDirectory() as tmp:
                db.save(tmp)
                db.load(tmp)
            self.assertIs(db.config, db._store.config)

        self.assertEqual(
            recorded,
            set(self.SERVING_CALLS.values()),
            "some servable donor-surface item was NOT served by its IslandsStore member: "
            f"{sorted(set(self.SERVING_CALLS.values()) - recorded)}",
        )

    def test_state_writes_route_to_the_store_too(self):
        """Writes are the half a read-only recording would miss."""
        recorded = set()
        writable = ["current_island", "best_program_id", "island_generations"]
        with contextlib.ExitStack() as stack:
            for member in writable:
                stack.enter_context(
                    mock.patch.object(
                        IslandsStore,
                        member,
                        self._recording(member, getattr(IslandsStore, member), recorded),
                    )
                )
            db = AdapterProgramDatabase(_config())
            db.add(_program("p1", 0.5), target_island=0)
            db.current_island = 2
            db.best_program_id = "p1"
            db.island_generations = [4, 4, 4]

        self.assertEqual(recorded, {f"{member}=" for member in writable})


class TestWidenedCapabilitiesReturnUpstreamsOwnState(unittest.TestCase):
    """0188 Stage 7. The widened members hand back the container upstream owns.

    This is what makes "bug-for-bug faithful" structural instead of asserted: a
    donor write lands on real state, and a later read sees it — the way it does
    upstream. Stage 1 served these as views rebuilt per access, which absorbed
    donor mutations on a throwaway copy and manufactured a false (a) signal.
    """

    def test_container_writes_are_visible_to_later_reads(self):
        db = AdapterProgramDatabase(_config())
        db.add(_program("p1", 0.5), target_island=1)

        db.islands[1].add("ghost")
        self.assertIn("ghost", db.islands[1])
        db.islands[1].remove("ghost")

        db.programs["p2"] = _program("p2", 0.1)
        self.assertIn("p2", db.programs)
        del db.programs["p2"]
        self.assertNotIn("p2", db.programs)

        db.island_generations[0] = 7
        self.assertEqual(db.island_generations[0], 7)

    def test_island_best_programs_keeps_upstreams_stale_entry(self):
        # Upstream maintains this list incrementally, so deleting the program it
        # names leaves a STALE id behind. A derived "current best per island"
        # view would silently correct that — i.e. diverge from the pin. Donor
        # test_island_tracking.test_island_best_with_missing_program asserts the
        # stale behaviour, so serving upstream's own list is the faithful answer.
        db = AdapterProgramDatabase(_config())
        db.add(_program("p1", 0.9), target_island=0)
        self.assertEqual(db.island_best_programs[0], "p1")
        del db.programs["p1"]
        self.assertEqual(db.island_best_programs[0], "p1")

    def test_live_instance_method_override_is_routed_through_the_store(self):
        # Donor test_concurrent_island_access.py:194 replaces the bound method on
        # the live instance; test_island_isolation.py uses mock.patch.object for
        # the same thing. The override must land on the STORE, so the adapter's
        # own method still resolves it through the wrapper.
        db = AdapterProgramDatabase(_config())
        db.add(_program("p1", 0.5), target_island=0)
        sentinel = _program("patched", 1.0)

        db.sample_from_island = lambda island_id, num_inspirations=None: (sentinel, [])
        self.assertIn("sample_from_island", vars(db._store))
        parent, inspirations = db.sample_from_island(0, num_inspirations=2)
        self.assertIs(parent, sentinel)

        del db.sample_from_island
        self.assertNotIn("sample_from_island", vars(db._store))
        parent, _ = db.sample_from_island(0, num_inspirations=1)
        self.assertEqual(parent.id, "p1")

    def test_current_island_stays_off_the_store_instance(self):
        # tests/test_noema_islands_wrapper_fidelity_spec.py:844 asserts
        # `vars(store)` holds no current-island state (upstream issue #246).
        # Serving upstream's field must not quietly plant one on the wrapper.
        db = AdapterProgramDatabase(_config())
        db.current_island = 2
        self.assertEqual(db.current_island, 2)
        self.assertFalse([name for name in vars(db._store) if "current" in name])


class TestUnservedSurfaceStillFailsLoudly(unittest.TestCase):
    """0188 Stage 7 emptied the unserved list of everything the donor suite
    touches. What remains are real ``ProgramDatabase`` members that no donor
    test in scope reaches — kept so the loud-failure mechanism itself stays
    proven, and so the next widening still gets a named capability rather than
    an AttributeError."""

    UNSERVED_READS = [
        "get_island_stats",
        "log_prompt",
        "_is_novel",
        "_update_archive",
        "_enforce_population_limit",
    ]

    def test_unserved_reads_raise_naming_the_capability(self):
        db = AdapterProgramDatabase(_config())
        for name in self.UNSERVED_READS:
            with self.assertRaises(NotImplementedError, msg=name) as ctx:
                getattr(db, name)
            self.assertIn(name, str(ctx.exception))

    def test_unserved_writes_raise_instead_of_silently_absorbing(self):
        db = AdapterProgramDatabase(_config())
        with self.assertRaises(NotImplementedError) as ctx:
            db.feature_stats = {"x": 1}
        self.assertIn("feature_stats", str(ctx.exception))

    def test_read_only_capabilities_reject_a_write_by_name(self):
        db = AdapterProgramDatabase(_config())
        with self.assertRaises(NotImplementedError) as ctx:
            db.programs = {}
        self.assertIn("read-only", str(ctx.exception))

    def test_unserved_deletes_raise_instead_of_bare_attributeerror(self):
        # mock.patch.object teardown calls delattr; a bare AttributeError there
        # hides which donor capability was actually missing.
        db = AdapterProgramDatabase(_config())
        with self.assertRaises(NotImplementedError) as ctx:
            del db.get_island_stats
        self.assertIn("get_island_stats", str(ctx.exception))

    def test_metric_kwarg_is_served_now_and_ranks_by_that_metric(self):
        # Was a declared deviation ("Noema ranks by the single get_fitness_score
        # convention"); Stage 7 routes it, and it must rank by the NAMED metric,
        # not silently by the default one.
        db = AdapterProgramDatabase(_config())
        db.add(Program(id="a", code="", metrics={"combined_score": 0.9, "accuracy": 0.1}))
        db.add(Program(id="b", code="", metrics={"combined_score": 0.1, "accuracy": 0.9}))
        self.assertEqual([p.id for p in db.get_top_programs(2, metric="accuracy")], ["b", "a"])
        self.assertEqual(db.get_best_program(metric="accuracy").id, "b")


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
