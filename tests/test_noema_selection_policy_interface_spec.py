"""Red contract for store-independent parent-selection policies.

Task 0074 separates population topology, selection, and coordination.  These
tests deliberately fail until that interface exists.  In particular,
Boltzmann may depend on ``SelectionPolicy`` and neutral population views, but
must not import or be constructed by a concrete store.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import unittest
from abc import ABCMeta


def require_symbol(testcase, module_name, symbol):
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        testcase.fail(f"missing planned module {module_name}: {exc}")
    if not hasattr(module, symbol):
        testcase.fail(f"missing planned contract {module_name}.{symbol}")
    return getattr(module, symbol)


def public_names(cls):
    names = {name for name in dir(cls) if not name.startswith("_")}
    names.update(getattr(cls, "__annotations__", ()))
    if dataclasses.is_dataclass(cls):
        names.update(field.name for field in dataclasses.fields(cls))
    return names


class TestSelectionPolicyContract(unittest.TestCase):
    def test_selection_policy_is_a_runtime_protocol_or_abstract_base(self):
        SelectionPolicy = require_symbol(
            self, "noema.substrate.base", "SelectionPolicy"
        )
        runtime_protocol = bool(
            getattr(SelectionPolicy, "_is_protocol", False)
            and getattr(SelectionPolicy, "_is_runtime_protocol", False)
        )
        abstract_base = isinstance(SelectionPolicy, ABCMeta) and inspect.isabstract(
            SelectionPolicy
        )
        self.assertTrue(runtime_protocol or abstract_base)

    def test_policy_contract_owns_selection_lifecycle_and_state(self):
        SelectionPolicy = require_symbol(
            self, "noema.substrate.base", "SelectionPolicy"
        )
        required = {
            "select",
            "on_child_accepted",
            "on_child_rejected",
            "state_dict",
            "load_state_dict",
            "required_capabilities",
        }
        self.assertFalse(required - public_names(SelectionPolicy))

    def test_policy_public_contract_contains_no_concrete_store_names(self):
        SelectionPolicy = require_symbol(
            self, "noema.substrate.base", "SelectionPolicy"
        )
        source = inspect.getsource(SelectionPolicy).casefold()
        self.assertNotIn("islandsstore", source)
        self.assertNotIn("treestore", source)

    def test_interface_runtime_composes_peer_store_and_policy(self):
        SubstrateRuntime = require_symbol(
            self, "noema.substrate.base", "SubstrateRuntime"
        )
        names = public_names(SubstrateRuntime)
        self.assertTrue({"store", "policy", "select"}.issubset(names))


class TestIndependentConfiguration(unittest.TestCase):
    def test_store_and_selection_are_peer_config_objects(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        config = NoemaConfig()

        self.assertIsInstance(config.substrate, SubstrateConfig)
        self.assertIsInstance(config.selection, SelectionConfig)
        self.assertFalse(hasattr(config.substrate, "sampling"))
        self.assertFalse(hasattr(config.substrate, "selection"))

    def test_omitted_configuration_resolves_native_islands_default(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        resolve = require_symbol(
            self, "noema.substrate.registry", "resolve_selection_policy"
        )
        config = NoemaConfig.from_dict({})

        self.assertEqual(config.substrate.kind, "islands")
        self.assertEqual(config.selection.policy, "substrate_default")
        self.assertEqual(
            resolve(config.substrate, config.selection), "stock_openevolve"
        )

    def test_boltzmann_has_no_concrete_store_import(self):
        try:
            module = importlib.import_module("noema.substrate.selection.boltzmann")
        except ImportError as exc:
            self.fail(f"missing planned Boltzmann policy module: {exc}")
        source = inspect.getsource(module)
        self.assertNotIn("noema.substrate.islands", source)
        self.assertNotIn("noema.substrate.tree", source)


if __name__ == "__main__":
    unittest.main()
