# 0034 · role-structured-benchmark-layout

Restructured `examples/circle_packing/initial_program.py` into F_imm (entry
point + helpers) outside `EVOLVE-BLOCK-START/END`, F_mut (strategy) inside.
Found the parse path (`apply_diff`, `parse_full_rewrite`) doesn't enforce the
boundary at all, so added `noema/substrate/boundary.py::enforce_immutable_boundary`
(called from `controller.py`'s retry loop) to restore F_imm from the parent or
reject the mutation; it's a no-op for programs without an evolve block.

Added 2 tests in `tests/test_noema_substrate_boundary.py`; `python3 -m
unittest discover tests` → 127 passed (125 pre-existing + 2 new), 0 failed,
no existing test modified.
