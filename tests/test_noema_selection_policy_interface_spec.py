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
            self, "noema.substrates.base", "SelectionPolicy"
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
            self, "noema.substrates.base", "SelectionPolicy"
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
            self, "noema.substrates.base", "SelectionPolicy"
        )
        source = inspect.getsource(SelectionPolicy).casefold()
        self.assertNotIn("islandsstore", source)
        self.assertNotIn("treestore", source)

    def test_interface_runtime_composes_peer_store_and_policy(self):
        SubstrateRuntime = require_symbol(
            self, "noema.substrates.base", "SubstrateRuntime"
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
            self, "noema.substrates.registry", "resolve_selection_policy"
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
        self.assertNotIn("noema.substrates.islands", source)
        self.assertNotIn("noema.substrates.tree", source)


class TestBoltzmannCrossSubstrateComposition(unittest.TestCase):
    """Boltzmann selection composes with all three store kinds.

    Tree and CVT expose `sampling_weights` in their capabilities frozenset
    because they persist program.metadata verbatim, which is all Boltzmann
    needs to read and write sample_weight values.  The default policy for
    tree remains UCT and for CVT remains cvt_ucb; Boltzmann is opt-in via
    explicit `selection: policy: boltzmann` in the experiment YAML."""

    def test_tree_substrate_with_boltzmann_selection_composes_cleanly(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        build_substrate_runtime = require_symbol(
            self, "noema.substrates.registry", "build_substrate_runtime"
        )

        config = NoemaConfig(
            substrate=SubstrateConfig(kind="tree"),
            selection=SelectionConfig(policy="boltzmann"),
        )

        runtime = build_substrate_runtime(config)
        self.assertEqual(runtime.policy.__class__.__name__, "BoltzmannSelectionPolicy")

    def test_islands_with_boltzmann_selection_composes_cleanly(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        build_substrate_runtime = require_symbol(
            self, "noema.substrates.registry", "build_substrate_runtime"
        )

        config = NoemaConfig(
            substrate=SubstrateConfig(kind="islands"),
            selection=SelectionConfig(policy="boltzmann"),
        )

        runtime = build_substrate_runtime(config)
        self.assertEqual(runtime.policy.__class__.__name__, "BoltzmannSelectionPolicy")

    def test_cvt_substrate_with_boltzmann_selection_composes_cleanly(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        build_substrate_runtime = require_symbol(
            self, "noema.substrates.registry", "build_substrate_runtime"
        )

        config = NoemaConfig(
            substrate=SubstrateConfig(kind="cvt"),
            selection=SelectionConfig(policy="boltzmann"),
        )

        runtime = build_substrate_runtime(config)
        self.assertEqual(runtime.policy.__class__.__name__, "BoltzmannSelectionPolicy")

    def test_tree_declares_sampling_weights_capability(self):
        from noema.substrates.tree import TreeStore

        self.assertIn("sampling_weights", TreeStore.capabilities)

    def test_cvt_declares_sampling_weights_capability(self):
        from noema.substrates.cvt import CVTStore

        self.assertIn("sampling_weights", CVTStore.capabilities)


def _prog(pid, score, parent_id=None):
    from openevolve.database import Program

    return Program(
        id=pid,
        code=f"def {pid.replace('-', '_')}():\n    return {score}\n",
        language="python",
        parent_id=parent_id,
        metrics={"combined_score": score},
    )


_LOOPY = "def f():\n    t=0\n    for i in range(1000):\n        for j in range(9): t+=i*j\n    return t\n"
_COMPY = "def f():\n    return sum(i*2 for i in range(10))\n"


class TestBoltzmannRuntimeSelect(unittest.TestCase):
    """Verify Boltzmann actually runs select() on Tree and CVT stores end-to-end.

    Composition tests confirm the policy attaches; these confirm the interface
    holds at runtime: store methods are called, a valid Selection is returned,
    and on_child_accepted writes sample_weight into program.metadata."""

    def test_boltzmann_select_returns_valid_selection_on_tree_store(self):
        import numpy as np
        from noema.substrates.tree import TreeStore
        from noema.selection.boltzmann import BoltzmannSelectionPolicy

        store = TreeStore(steps_per_generation=1)
        seed = _prog("seed", 1.0)
        child = _prog("child", 0.8, parent_id="seed")
        store.add(seed)
        store.add(child)

        policy = BoltzmannSelectionPolicy(rng=np.random.RandomState(0))
        selection = policy.select(store)

        self.assertIsNotNone(selection.parent)
        self.assertIn(selection.parent.id, {"seed", "child"})

    def test_boltzmann_select_returns_valid_selection_on_cvt_store(self):
        import numpy as np
        from noema.substrates.cvt import CVTStore
        from noema.selection.boltzmann import BoltzmannSelectionPolicy

        import dataclasses

        store = CVTStore(n_centroids=64, seed=7, feature_dimensions=["x"])
        p1 = dataclasses.replace(_prog("p1", 0.5), code=_LOOPY)
        p2 = dataclasses.replace(_prog("p2", 0.9), code=_COMPY)
        store.add(p1)
        store.add(p2)

        policy = BoltzmannSelectionPolicy(rng=np.random.RandomState(0))
        selection = policy.select(store)

        self.assertIsNotNone(selection.parent)
        self.assertIn(selection.parent.id, {"p1", "p2"})

    def test_boltzmann_on_child_accepted_writes_sample_weight_to_metadata(self):
        import numpy as np
        from noema.substrates.tree import TreeStore
        from noema.selection.boltzmann import BoltzmannSelectionPolicy

        store = TreeStore(steps_per_generation=1)
        seed = _prog("seed", 1.0)
        child = _prog("child", 0.8, parent_id="seed")
        store.add(seed)
        store.add(child)

        policy = BoltzmannSelectionPolicy(rng=np.random.RandomState(0))
        policy.select(store)
        policy.on_child_accepted(parent=seed, child=child, step_size=1.0)

        self.assertIn("sample_weight", child.metadata)
        self.assertIsInstance(child.metadata["sample_weight"], float)
        self.assertGreater(child.metadata["sample_weight"], 0)


if __name__ == "__main__":
    unittest.main()
