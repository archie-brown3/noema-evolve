"""End-to-end controller run on the CVT substrate (task 0108 done-when #1).

Reuses the controller harness from test_noema_controller: fake mutation LLM,
number-returning evaluator, null coordination — only the substrate changes.
"""

import asyncio
import os
import tempfile
import unittest

from noema.config import NoemaConfig, SubstrateConfig
from noema.cvt import CVTStore

from tests.test_noema_controller import make_config, make_controller


def cvt_config(**overrides) -> NoemaConfig:
    overrides.setdefault(
        "substrate", SubstrateConfig(kind="cvt", cvt_n_centroids=64)
    )
    return make_config(**overrides)


class TestCVTControllerEndToEnd(unittest.TestCase):
    def test_null_arm_runs_end_to_end_on_cvt(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, ledger, client = make_controller(tmp, config=cvt_config())
            best = asyncio.run(controller.run())

            # The store really is CVT and evolution happened.
            self.assertIsInstance(controller.db, CVTStore)
            self.assertEqual(controller.db.topology, "cvt_regions")
            self.assertGreater(controller.db.num_programs, 1)
            self.assertIsNotNone(best)
            self.assertGreater(best.metrics["combined_score"], 0.1)

            # Metering guarantee: every mutation billed, ZERO substrate/coordination spend.
            self.assertEqual(len(client.calls), 6)
            self.assertEqual(ledger.spent("mutation"), 6 * 140)
            self.assertEqual(ledger.spent("coordination"), 0)

    def test_checkpoint_resume_on_cvt(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _ = make_controller(tmp, config=cvt_config())
            asyncio.run(controller.run(iterations=3))
            n_before = controller.db.num_programs

            checkpoint = os.path.join(tmp, "output", "checkpoints")
            latest = sorted(os.listdir(checkpoint))[-1]
            self.assertTrue(
                os.path.exists(os.path.join(checkpoint, latest, "cvt_store.json"))
            )

            # A fresh controller resumes from the checkpoint with the corpus intact.
            controller2, _, _ = make_controller(tmp, config=cvt_config())
            controller2.load_checkpoint(os.path.join(checkpoint, latest))
            self.assertEqual(controller2.db.num_programs, n_before)


if __name__ == "__main__":
    unittest.main()
