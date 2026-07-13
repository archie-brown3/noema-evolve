"""Executable specification for the coordination/substrate boundary.

These tests are intentionally red until the substrate seam is implemented.
They describe the API required by the mechanism x substrate study: substrate
selection is configured independently of the coordination arm, and a module
may request selection influence only through a pre-selection, read-only hook.
"""

import dataclasses
import importlib
import inspect
import unittest


def require_symbol(testcase, module_name, symbol):
    """Load a future contract symbol while keeping unittest discovery usable."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        testcase.fail(f"missing planned module {module_name}: {exc}")
    if not hasattr(module, symbol):
        testcase.fail(f"missing planned contract {module_name}.{symbol}")
    return getattr(module, symbol)


class TestNeutralCoordinationContextSpec(unittest.TestCase):
    def test_generation_context_uses_neutral_population_snapshots(self):
        GenerationContext = require_symbol(
            self, "noema.coordination.base", "GenerationContext"
        )
        names = {field.name for field in dataclasses.fields(GenerationContext)}
        self.assertIn("scope_id", names)
        self.assertIn("local_population", names)
        self.assertIn("global_population", names)
        self.assertNotIn("island", names)
        self.assertNotIn("island_fitnesses", names)

    def test_context_exposes_snapshots_not_a_mutable_store(self):
        GenerationContext = require_symbol(
            self, "noema.coordination.base", "GenerationContext"
        )
        names = {field.name for field in dataclasses.fields(GenerationContext)}
        self.assertFalse(
            {"store", "database", "substrate"} & names,
            "coordination receives immutable observations, never the substrate object",
        )


class TestPreSelectionHookSpec(unittest.TestCase):
    def test_sampling_request_is_a_sync_nonabstract_default_hook(self):
        CoordinationModule = require_symbol(
            self, "noema.coordination.base", "CoordinationModule"
        )
        hook = getattr(CoordinationModule, "sampling_request", None)
        self.assertIsNotNone(hook, "selection influence must happen before sampling")
        self.assertFalse(inspect.iscoroutinefunction(hook))
        self.assertFalse(getattr(hook, "__isabstractmethod__", False))

    def test_null_sampling_request_is_empty(self):
        NullCoordination = require_symbol(
            self, "noema.coordination.base", "NullCoordination"
        )
        SelectionContext = require_symbol(
            self, "noema.coordination.base", "SelectionContext"
        )
        request = NullCoordination().sampling_request(
            SelectionContext(iteration=0, generation=0, global_population=None)
        )
        self.assertEqual(dict(request.hints), {})

    def test_post_selection_advice_no_longer_carries_sampling_hint(self):
        Advice = require_symbol(self, "noema.coordination.base", "Advice")
        names = {field.name for field in dataclasses.fields(Advice)}
        self.assertNotIn(
            "sampling_hint", names,
            "a hint returned after parent selection is ambiguous and unusable",
        )


class TestFactorIndependenceSpec(unittest.TestCase):
    def test_substrate_and_selection_have_separate_typed_config(self):
        SubstrateConfig = require_symbol(self, "noema.config", "SubstrateConfig")
        SelectionConfig = require_symbol(self, "noema.config", "SelectionConfig")
        substrate = SubstrateConfig()
        selection = SelectionConfig()
        self.assertEqual(substrate.kind, "islands")
        self.assertFalse(hasattr(substrate, "sampling"))
        self.assertEqual(selection.policy, "substrate_default")

    def test_coordination_choice_does_not_choose_the_substrate(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        CoordinationConfig = require_symbol(self, "noema.config", "CoordinationConfig")
        null = NoemaConfig(coordination=CoordinationConfig(module="null"))
        faithful = NoemaConfig(coordination=CoordinationConfig(module="pes-faithful"))
        null_dict = null.to_dict()
        faithful_dict = faithful.to_dict()
        self.assertIn("substrate", null_dict)
        self.assertIn("substrate", faithful_dict)
        self.assertIn("selection", null_dict)
        self.assertIn("selection", faithful_dict)
        self.assertEqual(null_dict["substrate"], faithful_dict["substrate"])
        self.assertEqual(null_dict["selection"], faithful_dict["selection"])

    def test_old_config_defaults_to_native_openevolve_islands(self):
        NoemaConfig = require_symbol(self, "noema.config", "NoemaConfig")
        config = NoemaConfig.from_dict({"random_seed": 42})
        self.assertTrue(
            hasattr(config, "substrate"),
            "old configurations must acquire the native islands default",
        )
        self.assertEqual(config.substrate.kind, "islands")
        self.assertEqual(config.selection.policy, "substrate_default")


if __name__ == "__main__":
    unittest.main()
