# 0025 · fix-ledger-metering-local-inference

Fixed `BudgetedLLM.generate_with_context` (`noema/budget/llm.py`): a `usage`
envelope with present-but-`None` `prompt_tokens`/`completion_tokens` (the
local llama.cpp/vLLM proxy shape) was silently collapsing to a charged zero
via `getattr(usage, "prompt_tokens", 0) or 0`. Now only the null field is
estimated from the actual prompt/response text and the row is flagged
`estimated: True`; a real reported field is kept exactly. Usage-entirely-absent
still charges zero (unchanged, matches the pre-existing test).

Added `estimated: bool = False` to `CallRecord` in `noema/budget/ledger.py` —
outside the work order's named file list, but there was no way to carry
`estimated: true` per-row without it; documented as a scope deviation in the
task file.

Extended `tests/test_noema_budgeted_llm.py` (no existing assertions removed):
2 new tests for the null-usage-fields and partial-usage cases, plus one new
assertion on the existing real-usage test. `unittest tests.test_noema_budgeted_llm`
→ 11 passed; `unittest discover tests` → 106 passed, 0 failed.

Not run: live LLM smoke test against a local node, and the
`ledger-completeness-live` standing-goal re-check — both queue-tier, left for
the user per the work order.

**Note**: `unittest discover tests` regenerated unrelated tracked `.pyc` files
under `noema/coordination/{hifo,pes}/__pycache__`; this sandbox blocked
`git checkout`/`git restore` to revert them, so they may appear as incidental
diff noise unrelated to this change.
