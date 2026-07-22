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
            self, "noema.base", "SelectionPolicy"
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
            self, "noema.base", "SelectionPolicy"
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
            self, "noema.base", "SelectionPolicy"
        )
        source = inspect.getsource(SelectionPolicy).casefold()
        self.assertNotIn("islandsstore", source)
        self.assertNotIn("treestore", source)

    def test_interface_runtime_composes_peer_store_and_policy(self):
        SubstrateRuntime = require_symbol(
            self, "noema.base", "SubstrateRuntime"
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
            self, "noema.registry", "resolve_selection_policy"
        )
        config = NoemaConfig.from_dict({})

        self.assertEqual(config.substrate.kind, "islands")
        self.assertEqual(config.selection.policy, "substrate_default")
        self.assertEqual(
            resolve(config.substrate, config.selection), "stock_openevolve"
        )

    def test_boltzmann_has_no_concrete_store_import(self):
        try:
            module = importlib.import_module("noema.selection.boltzmann")
        except ImportError as exc:
            self.fail(f"missing planned Boltzmann policy module: {exc}")
        source = inspect.getsource(module)
        self.assertNotIn("noema.islands", source)
        self.assertNotIn("noema.tree", source)


class TestTreeBoltzmannCompositionFailure(unittest.TestCase):
    """task 0086 / Decision #18's gated Boltzmann probe: TreeStore does not
    declare `sampling_weights` (Boltzmann's whole mechanism is weighting the
    sampling distribution — a tree has no such concept, only islands' MAP-
    Elites archive does), so this combination must fail loudly at
    composition, not run degraded or silently ignore the weighting. Offline:
    no LLM call, no controller, no run — this is the only part of 0086
    that's agent-executable before the headline matrix exists and the user
    approves the probe run budget (see the ticket)."""

    def test_tree_substrate_with_boltzmann_selection_raises_at_composition(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        build_substrate_runtime = require_symbol(
            self, "noema.registry", "build_substrate_runtime"
        )

        config = NoemaConfig(
            substrate=SubstrateConfig(kind="tree"),
            selection=SelectionConfig(policy="boltzmann"),
        )

        with self.assertRaises(ValueError) as cm:
            build_substrate_runtime(config)
        self.assertIn("sampling_weights", str(cm.exception))

    def test_islands_with_boltzmann_selection_composes_cleanly(self):
        # Negative control: the same policy against the substrate it's
        # actually designed for must NOT raise — proves the failure above is
        # about the specific tree/boltzmann capability gap, not boltzmann
        # being broken in general.
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        build_substrate_runtime = require_symbol(
            self, "noema.registry", "build_substrate_runtime"
        )

        config = NoemaConfig(
            substrate=SubstrateConfig(kind="islands"),
            selection=SelectionConfig(policy="boltzmann"),
        )

        runtime = build_substrate_runtime(config)
        self.assertEqual(runtime.policy.__class__.__name__, "BoltzmannSelectionPolicy")

    def test_tree_declares_the_capabilities_boltzmann_actually_needs_except_weights(self):
        # Precision check: the failure must be specifically about
        # sampling_weights, not some other/wider mismatch that would make
        # this test pass for the wrong reason.
        from noema.selection.boltzmann import BoltzmannSelectionPolicy
        from noema.tree import TreeStore

        missing = BoltzmannSelectionPolicy.required_capabilities - TreeStore.capabilities
        self.assertEqual(missing, frozenset({"sampling_weights"}))


if __name__ == "__main__":
    unittest.main()
