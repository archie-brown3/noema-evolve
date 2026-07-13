"""
Tests for noema.boundary — the F_imm / F_mut evolve-block boundary
enforced on parsed mutation output (Tree Substrate Plan Phase A).
"""

import unittest

from noema.boundary import enforce_immutable_boundary

PARENT_CODE = (
    "def entry_point():\n"
    "    return strategy()\n"
    "\n"
    "# EVOLVE-BLOCK-START\n"
    "def strategy():\n"
    "    return 1\n"
    "# EVOLVE-BLOCK-END\n"
    "\n"
    "def helper():\n"
    "    return 0\n"
)


class TestEnforceImmutableBoundary(unittest.TestCase):
    def test_mutation_touching_f_imm_is_restored_byte_identical(self):
        # A "full rewrite" that clobbers F_imm (entry_point's body and the
        # trailing helper) while leaving the evolve block alone.
        child_code = (
            "def entry_point():\n"
            "    return 999\n"
            "\n"
            "# EVOLVE-BLOCK-START\n"
            "def strategy():\n"
            "    return 1\n"
            "# EVOLVE-BLOCK-END\n"
            "\n"
            "def helper():\n"
            "    return -1\n"
        )
        restored = enforce_immutable_boundary(PARENT_CODE, child_code)

        parent_lines = PARENT_CODE.split("\n")
        restored_lines = restored.split("\n")
        f_imm_parent = parent_lines[:3] + parent_lines[6:]
        f_imm_restored = restored_lines[:3] + restored_lines[6:]
        self.assertEqual(f_imm_parent, f_imm_restored)

    def test_f_mut_interface_survives_m1_style_diff(self):
        # An m1-style diff only ever emits the new full text for the region
        # it touched; here it's the evolve-block interior changing strategy()
        # to add a new helper while keeping the call signature intact.
        child_code = (
            "def entry_point():\n"
            "    return strategy()\n"
            "\n"
            "# EVOLVE-BLOCK-START\n"
            "def strategy():\n"
            "    return _inner() + 1\n"
            "\n"
            "def _inner():\n"
            "    return 41\n"
            "# EVOLVE-BLOCK-END\n"
            "\n"
            "def helper():\n"
            "    return 0\n"
        )
        restored = enforce_immutable_boundary(PARENT_CODE, child_code)

        namespace = {}
        exec(restored, namespace)
        self.assertEqual(namespace["entry_point"](), 42)


if __name__ == "__main__":
    unittest.main()
