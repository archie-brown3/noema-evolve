"""CLI wiring for the flat HiFo-compatible substrate."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO, "examples", "bin_packing", "run_noema_arm.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("bin_packing_run_noema_arm", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeController:
    captured_config = None

    def __init__(self, config, **kwargs):
        type(self).captured_config = config

    async def run(self):
        return None


class TestBinPackingFlatSubstrateFlag(unittest.TestCase):
    def test_flat_substrate_reaches_the_run_config(self):
        module = _load_script()
        module.NoemaController = _FakeController
        with tempfile.TemporaryDirectory() as output_dir:
            old_argv = sys.argv
            sys.argv = [
                "run_noema_arm.py",
                "--arm", "null",
                "--api-base", "http://localhost:9/v1",
                "--output-dir", output_dir,
                "--substrate", "flat",
            ]
            try:
                module.main()
            finally:
                sys.argv = old_argv

        self.assertEqual(_FakeController.captured_config.substrate.kind, "flat")
        self.assertEqual(
            _FakeController.captured_config.selection.policy, "substrate_default"
        )

