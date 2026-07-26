"""
CLI-level test for the circle_packing run script's substrate/selection/api-key
wiring (task 0103/GH #44 remainder): before this, run_noema_arm.py had no way
to select anything but the islands substrate, so no tree+UCT cell of the
Phase L matrix could be launched at all.
"""

import importlib.util
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO, "examples", "circle_packing", "run_noema_arm.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("run_noema_arm", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeController:
    captured_config = None

    def __init__(self, config, evaluation_file, initial_program_code, output_dir):
        _FakeController.captured_config = config

    async def run(self):
        return None


class TestRunNoemaArmSubstrateSelectionFlags(unittest.TestCase):
    def setUp(self):
        self.module = _load_script()
        self.module.NoemaController = _FakeController
        self.tmpdir = tempfile.mkdtemp()
        self.base_argv = [
            "run_noema_arm.py",
            "--arm", "null",
            "--api-base", "http://localhost:9/v1",
            "--output-dir", self.tmpdir,
            "--iterations", "1",
        ]

    def _run_with(self, extra_argv):
        old_argv = sys.argv
        sys.argv = self.base_argv + extra_argv
        try:
            self.module.main()
        finally:
            sys.argv = old_argv
        return _FakeController.captured_config

    def test_default_substrate_is_islands(self):
        config = self._run_with([])
        self.assertEqual(config.substrate.kind, "islands")
        self.assertEqual(config.selection.policy, "substrate_default")

    def test_substrate_flag_selects_tree(self):
        config = self._run_with(["--substrate", "tree"])
        self.assertEqual(config.substrate.kind, "tree")

    def test_selection_flag_overrides_substrate_default(self):
        config = self._run_with(["--substrate", "tree", "--selection", "boltzmann"])
        self.assertEqual(config.selection.policy, "boltzmann")

    def test_api_key_flag_is_wired(self):
        config = self._run_with(["--api-key", "sk-test-123"])
        self.assertEqual(config.llm.mutation.api_key, "sk-test-123")
        self.assertEqual(config.llm.coordination.api_key, "sk-test-123")

    def test_api_key_defaults_to_none(self):
        config = self._run_with([])
        self.assertEqual(config.llm.mutation.api_key, "none")


if __name__ == "__main__":
    unittest.main()
