"""Compatibility checks for the public parent-selection package API."""

import unittest


class TestSelectionPackageExports(unittest.TestCase):
    def test_established_selection_classes_remain_public(self):
        from noema.selection import (
            BoltzmannSelectionPolicy,
            StockOpenEvolveSelection,
            UCTSelectionPolicy,
        )

        self.assertEqual(StockOpenEvolveSelection.__name__, "StockOpenEvolveSelection")
        self.assertEqual(BoltzmannSelectionPolicy.__name__, "BoltzmannSelectionPolicy")
        self.assertEqual(UCTSelectionPolicy.__name__, "UCTSelectionPolicy")


if __name__ == "__main__":
    unittest.main()
