"""Failing specification for the future substrate population-store seam.

These tests deliberately describe an interface which has not landed yet.  Keep
the future imports in test helpers: ``unittest discover`` must continue to run
while ``noema.substrate.base`` is absent.  The failures become ordinary passes,
without test edits, when the corresponding interface is implemented.
"""

from __future__ import annotations

import inspect
import json
import unittest
from abc import ABC, ABCMeta, abstractmethod
from dataclasses import fields, is_dataclass
from importlib import import_module


def _future_symbols(testcase: unittest.TestCase):
    """Import the proposed interface with an actionable specification failure."""

    try:
        module = import_module("noema.substrate.base")
    except ModuleNotFoundError as exc:
        if exc.name != "noema.substrate.base":
            raise
        testcase.fail(
            "future substrate interface missing: create noema.substrate.base with "
            "PopulationStore, Selection, and PopulationSnapshot"
        )

    missing = [
        name
        for name in ("PopulationStore", "Selection", "PopulationSnapshot")
        if not hasattr(module, name)
    ]
    if missing:
        testcase.fail(
            "future substrate interface missing symbol(s): " + ", ".join(missing)
        )
    return module.PopulationStore, module.Selection, module.PopulationSnapshot


def _public_member_names(cls) -> set[str]:
    names = {name for name in dir(cls) if not name.startswith("_")}
    names.update(getattr(cls, "__annotations__", ()))
    if is_dataclass(cls):
        names.update(field.name for field in fields(cls))
    return names


def _public_parameter_names(cls) -> set[str]:
    names: set[str] = set()
    for member_name in _public_member_names(cls):
        member = getattr(cls, member_name, None)
        if not callable(member):
            continue
        try:
            names.update(inspect.signature(member).parameters)
        except (TypeError, ValueError):
            pass
    return names


class _PopulationStoreContractMixin(ABC):
    """Reusable behavioural contract for every concrete population store.

    The leading underscore, lack of ``TestCase`` ancestry, and abstract factory
    prevent unittest from collecting this mixin directly.
    """

    @abstractmethod
    def make_store(self, *, steps_per_generation: int, scopes: tuple[str, ...]):
        raise NotImplementedError

    def test_selection_is_neutral_and_complete(self):
        _, Selection, _ = _future_symbols(self)
        parent = object()
        inspiration = object()
        selection = Selection(
            parent=parent,
            inspirations=(inspiration,),
            source_scope="source-partition",
            target_scope="target-partition",
        )

        self.assertIs(selection.parent, parent)
        self.assertEqual(selection.inspirations, (inspiration,))
        self.assertEqual(selection.source_scope, "source-partition")
        self.assertEqual(selection.target_scope, "target-partition")

    def test_snapshot_explicitly_distinguishes_local_and_global_scope(self):
        _, _, PopulationSnapshot = _future_symbols(self)
        local = PopulationSnapshot(
            scope="partition-a", top_programs=(), fitnesses=(), best_program=None
        )
        global_ = PopulationSnapshot(
            scope=None, top_programs=(), fitnesses=(), best_program=None
        )

        self.assertEqual(local.scope, "partition-a")
        self.assertIsNone(global_.scope)
        self.assertNotEqual(local.scope, global_.scope)

    def test_steps_per_generation_is_not_derived_from_scope_count(self):
        two_scopes = self.make_store(
            steps_per_generation=7, scopes=("partition-a", "partition-b")
        )
        five_scopes = self.make_store(
            steps_per_generation=7,
            scopes=tuple(f"partition-{index}" for index in range(5)),
        )

        self.assertEqual(two_scopes.steps_per_generation, 7)
        self.assertEqual(five_scopes.steps_per_generation, 7)

    def test_select_and_snapshot_return_neutral_value_objects(self):
        _, Selection, PopulationSnapshot = _future_symbols(self)
        store = self.make_store(steps_per_generation=3, scopes=("partition-a",))

        self.assertIsInstance(store.select(target_scope="partition-a"), Selection)
        self.assertIsInstance(store.snapshot(scope="partition-a"), PopulationSnapshot)
        self.assertIsInstance(store.snapshot(scope=None), PopulationSnapshot)


class _CheckpointStateContractMixin(ABC):
    """Reusable checkpoint contract, intentionally not a collected test case."""

    @abstractmethod
    def make_store(self, *, steps_per_generation: int, scopes: tuple[str, ...]):
        raise NotImplementedError

    def test_state_is_json_serializable_and_round_trips(self):
        original = self.make_store(
            steps_per_generation=11, scopes=("partition-a", "partition-b")
        )
        original.add("program-a", target_scope="partition-a")
        state = original.state_dict()
        encoded = json.dumps(state)

        restored = self.make_store(steps_per_generation=1, scopes=("unused",))
        restored.load_state_dict(json.loads(encoded))

        self.assertEqual(restored.state_dict(), state)
        self.assertEqual(restored.steps_per_generation, 11)
        self.assertEqual(
            restored.snapshot(scope="partition-a"),
            original.snapshot(scope="partition-a"),
        )


class TestPopulationStoreInterfaceSpec(
    _PopulationStoreContractMixin, _CheckpointStateContractMixin, unittest.TestCase
):
    """Concrete interface-level fixture for the expected-failing contracts."""

    def make_store(self, *, steps_per_generation: int, scopes: tuple[str, ...]):
        PopulationStore, Selection, PopulationSnapshot = _future_symbols(self)

        def init(store, *, steps_per_generation, scopes):
            store.steps_per_generation = steps_per_generation
            store.scopes = tuple(scopes)
            store._programs = {scope: [] for scope in scopes}

        def select(store, *, target_scope=None):
            programs = store._programs.get(target_scope, ())
            parent = programs[-1] if programs else None
            return Selection(
                parent=parent,
                inspirations=(),
                source_scope=target_scope,
                target_scope=target_scope,
            )

        def add(store, program, *, target_scope=None):
            store._programs.setdefault(target_scope, []).append(program)

        def snapshot(store, *, scope=None):
            if scope is None:
                programs = tuple(
                    program
                    for scoped_programs in store._programs.values()
                    for program in scoped_programs
                )
            else:
                programs = tuple(store._programs.get(scope, ()))
            return PopulationSnapshot(
                scope=scope,
                top_programs=programs,
                fitnesses=(),
                best_program=programs[-1] if programs else None,
            )

        def end_generation(store):
            return None

        def state_dict(store):
            return {
                "steps_per_generation": store.steps_per_generation,
                "scopes": list(store.scopes),
                "programs": {
                    str(scope): list(programs)
                    for scope, programs in store._programs.items()
                },
            }

        def load_state_dict(store, state):
            store.steps_per_generation = state["steps_per_generation"]
            store.scopes = tuple(state["scopes"])
            store._programs = {
                scope: list(state["programs"].get(scope, ()))
                for scope in store.scopes
            }

        implementation = type(
            "ContractPopulationStore",
            (PopulationStore,),
            {
                "__init__": init,
                "select": select,
                "add": add,
                "snapshot": snapshot,
                "end_generation": end_generation,
                "state_dict": state_dict,
                "load_state_dict": load_state_dict,
            },
        )
        return implementation(
            steps_per_generation=steps_per_generation, scopes=scopes
        )

    def test_population_store_supports_runtime_contract_checks(self):
        PopulationStore, _, _ = _future_symbols(self)
        is_runtime_protocol = bool(
            getattr(PopulationStore, "_is_protocol", False)
            and getattr(PopulationStore, "_is_runtime_protocol", False)
        )
        is_abstract_base = isinstance(PopulationStore, ABCMeta) and inspect.isabstract(
            PopulationStore
        )
        self.assertTrue(
            is_runtime_protocol or is_abstract_base,
            "PopulationStore must be either a @runtime_checkable Protocol or an ABC",
        )

        complete = self.make_store(steps_per_generation=4, scopes=("partition-a",))
        self.assertIsInstance(complete, PopulationStore)

    def test_population_store_declares_the_neutral_host_surface(self):
        PopulationStore, _, _ = _future_symbols(self)
        required = {
            "population",
            "elites",
            "native_select",
            "add",
            "snapshot",
            "end_generation",
            "state_dict",
            "load_state_dict",
            "steps_per_generation",
        }
        self.assertFalse(
            required - _public_member_names(PopulationStore),
            "PopulationStore is missing required public members: "
            + ", ".join(sorted(required - _public_member_names(PopulationStore))),
        )

    def test_public_interface_contains_no_island_named_members(self):
        PopulationStore, Selection, PopulationSnapshot = _future_symbols(self)
        offenders = []
        for cls in (PopulationStore, Selection, PopulationSnapshot):
            names = _public_member_names(cls) | _public_parameter_names(cls)
            offenders.extend(
                f"{cls.__name__}.{name}"
                for name in names
                if "island" in name.casefold()
            )
        self.assertEqual(
            offenders,
            [],
            "substrate interface must use neutral scope terminology; found "
            + ", ".join(sorted(offenders)),
        )


if __name__ == "__main__":
    unittest.main()
