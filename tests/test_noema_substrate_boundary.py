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


PARENT_CODE_WITH_IMPORT = (
    "import numpy as np\n"
    "\n"
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


class TestEnforceImmutableBoundaryMergeNewImports(unittest.TestCase):
    """merge_new_imports=True (task 0105): PES-faithful directive mode emits a
    full rewrite whose evolve-block strategy code may need a library F_imm
    never imported. These cover the opt-in merge path only — default (False)
    behaviour is the byte-identical-restore path already covered above."""

    def test_new_import_kept_f_imm_otherwise_unchanged(self):
        child_code = (
            "import numpy as np\n"
            "from scipy.optimize import linprog\n"
            "\n"
            "def entry_point():\n"
            "    return 999\n"  # would be a clobber under the old behaviour
            "\n"
            "# EVOLVE-BLOCK-START\n"
            "def strategy():\n"
            "    linprog\n"  # references the new import; NameError if stripped
            "    return 1\n"
            "# EVOLVE-BLOCK-END\n"
            "\n"
            "def helper():\n"
            "    return -1\n"
        )
        restored = enforce_immutable_boundary(
            PARENT_CODE_WITH_IMPORT, child_code, merge_new_imports=True
        )
        restored_lines = restored.split("\n")

        self.assertIn("from scipy.optimize import linprog", restored_lines)
        self.assertEqual(restored_lines.count("from scipy.optimize import linprog"), 1)

        # F_imm (entry point + helper) restored from the parent, not the
        # child's clobbering values — proven by executing the result, same
        # as test_f_mut_interface_survives_m1_style_diff above.
        namespace = {}
        exec(restored, namespace)
        self.assertEqual(namespace["entry_point"](), 1)  # parent's strategy(), not 999
        self.assertEqual(namespace["helper"](), 0)  # parent's helper, not -1

    def test_import_already_in_parent_is_not_duplicated(self):
        child_code = (
            "import numpy as np\n"  # already in parent
            "\n"
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
        restored = enforce_immutable_boundary(
            PARENT_CODE_WITH_IMPORT, child_code, merge_new_imports=True
        )
        self.assertEqual(restored.count("import numpy as np"), 1)

    def test_default_still_strips_the_import(self):
        child_code = (
            "import numpy as np\n"
            "from scipy.optimize import linprog\n"
            "\n"
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
        restored = enforce_immutable_boundary(PARENT_CODE_WITH_IMPORT, child_code)
        self.assertNotIn("scipy", restored)


if __name__ == "__main__":
    unittest.main()
