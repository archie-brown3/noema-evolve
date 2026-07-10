# Guarantee-triad audit — commits 887ac98, e161972, 3bfe346, 6f8797a, 56359f3

Read-only audit per work order. No files under noema/, tests/, examples/, or
openevolve/ were modified.

## 887ac98

Merge commit "Merge fix-ledger-metering: BudgetedLLM null-usage-field estimation".
Merge: 339acbb e161972. `git show --stat 887ac98` (default, vs. first parent)
resolves to the same content as e161972 (single-commit branch, non-divergent
merge):

```
 IMPLEMENTATION.md                | 28 ++++++++++++++++++++++++++++
 noema/budget/ledger.py           |  1 +
 noema/budget/llm.py              | 37 ++++++++++++++++++++++++++++++++++---
 tests/test_noema_budgeted_llm.py | 39 ++++++++++++++++++++++++++++++++++++++-
 4 files changed, 101 insertions(+), 4 deletions(-)
```

Files touched: `IMPLEMENTATION.md`, `noema/budget/ledger.py`, `noema/budget/llm.py`,
`tests/test_noema_budgeted_llm.py`.

Triad areas: **metering**. `noema/budget/ledger.py` adds `CallRecord.estimated:
bool` field. `noema/budget/llm.py` adds `_estimate_token_count()` and changes
`BudgetedLLM.generate_with_context` to estimate `prompt_tokens`/`completion_tokens`
from message/response text when the server's `usage` envelope has null token
fields (llama.cpp/vLLM-style local servers), flagging the `CallRecord` as
`estimated=True` instead of silently charging zero. No prompt-identity or
determinism-relevant code touched.

`tests/test_noema_budgeted_llm.py` is modified in this same commit: adds
`test_local_server_null_usage_fields_estimated` and
`test_partial_usage_keeps_real_field_and_estimates_missing_one`, and asserts
`rec.estimated` is `False` in the existing happy-path test.

triad-tests-extended-in-same-commit: YES

## e161972

`fix: enhance BudgetedLLM to estimate token counts for null usage fields and
add tests` — this is the single commit merged by 887ac98 (identical diff
content to the merge, see above); `git show --stat e161972`:

```
 IMPLEMENTATION.md                | 28 ++++++++++++++++++++++++++++
 noema/budget/ledger.py           |  1 +
 noema/budget/llm.py              | 37 ++++++++++++++++++++++++++++++++++---
 tests/test_noema_budgeted_llm.py | 39 ++++++++++++++++++++++++++++++++++++++-
 4 files changed, 101 insertions(+), 4 deletions(-)
```

Files touched: same as 887ac98 above.

Triad areas: **metering** (same as 887ac98 — this is the merged-in commit).

`tests/test_noema_budgeted_llm.py` is modified in this same commit (same test
additions as listed under 887ac98).

triad-tests-extended-in-same-commit: YES

## 3bfe346

`feat: implement intra-iteration retry mechanism with error feedback`.
`git show --stat 3bfe346`:

```
 loop/memory/STATE.md                   |   2 +
 noema/config.py                        |   4 +
 noema/controller.py                    | 116 ++++++++++++------
 spec/pes/stage-1-retry-loop.md         | 160 +++++++++++++++++++++++++
 spec/pes/stage-2-reflection-retries.md | 175 +++++++++++++++++++++++++++
 tests/test_noema_controller.py         | 209 +++++++++++++++++++++++++++++++++
 tests/test_noema_prompts.py            |  28 +++++
 7 files changed, 658 insertions(+), 36 deletions(-)
```

Files touched: `loop/memory/STATE.md`, `noema/config.py`, `noema/controller.py`,
`spec/pes/stage-1-retry-loop.md`, `spec/pes/stage-2-reflection-retries.md`,
`tests/test_noema_controller.py`, `tests/test_noema_prompts.py`.

Triad areas: **prompt identity** and **determinism**. `noema/controller.py`
wraps the per-iteration mutation call in a retry loop (`retry_cap` attempts);
on retry it builds a new prompt via `_build_retry_prompt()` which calls
`inject_advice()` again and appends a `_build_retry_suffix()` error-feedback
block to `prompt["user"]`, i.e. new prompt-construction logic feeding the
mutation LLM. The deterministic `child_id = f"it{iteration:06d}"` line and its
justifying comment are moved (not changed) into the loop; the retry loop can
now issue multiple `evaluate_program`/LLM calls for a single iteration, which
is a determinism-relevant control-flow change (previously exactly one call per
iteration).

`tests/test_noema_controller.py` and `tests/test_noema_prompts.py` are both
modified in this same commit (209 and 28 added lines respectively — new tests
for the retry loop and prompt).

triad-tests-extended-in-same-commit: YES

## 6f8797a

`feat: enhance retry mechanism with causal reflection for PES coordination`.
`git show --stat 6f8797a`:

```
 noema/controller.py                   | 11 ++++---
 noema/coordination/base.py            | 12 ++++++++
 noema/coordination/pes/module.py      | 29 +++++++++++++++---
 tests/test_noema_controller.py        | 57 +++++++++++++++++++++++++++++++++++
 tests/test_noema_coordination_base.py | 25 ++++++++++++++-
 tests/test_noema_pes.py               | 45 +++++++++++++++++++++++++++
 tests/test_noema_prompts.py           | 13 ++++++++
 7 files changed, 182 insertions(+), 10 deletions(-)
```

Files touched: `noema/controller.py`, `noema/coordination/base.py`,
`noema/coordination/pes/module.py`, `tests/test_noema_controller.py`,
`tests/test_noema_coordination_base.py`, `tests/test_noema_pes.py`,
`tests/test_noema_prompts.py`.

Triad areas: **prompt identity**. `noema/controller.py`'s `_build_retry_prompt`
becomes `async` and appends a new `reflection_suffix` (from
`self.coordination.retry_advice(ctx, error_text, attempt)`) to `prompt["user"]`
alongside the existing error-feedback suffix — another prompt-construction
change. `noema/coordination/base.py` adds a new `async def retry_advice(...)
-> str` hook to `CoordinationModule` (default no-op, `""`); this is the
sanctioned base.py interface addition referenced in CLAUDE.md (see `## base.py`
section below) and is not itself prompt/metering/determinism code, but it is
the seam prompt-identity content flows through. `noema/coordination/pes/module.py`
implements the PES override of `retry_advice`.

`tests/test_noema_controller.py`, `tests/test_noema_coordination_base.py`,
`tests/test_noema_pes.py`, and `tests/test_noema_prompts.py` are all modified
in this same commit.

triad-tests-extended-in-same-commit: YES

## 56359f3

`chore: add --budget-tokens/--retry CLI args to run_noema_arm.py`.
`git show --stat 56359f3`:

```
 examples/circle_packing/run_noema_arm.py | 7 ++++++-
 loop/memory/STATE.md                     | 2 ++
 2 files changed, 8 insertions(+), 1 deletion(-)
```

Files touched: `examples/circle_packing/run_noema_arm.py`, `loop/memory/STATE.md`.

Triad areas: none. This commit only adds CLI argument plumbing
(`--budget-tokens`, `--retry-enabled`, `--retry-cap`) in an example runner
script, wiring existing `BudgetConfig`/`retry_enabled`/`retry_cap` fields
already defined in `noema/config.py` through to the CLI. It does not touch
`noema/`, `tests/`, or `openevolve/` prompt/metering/determinism code.

triad-tests-extended-in-same-commit: N/A — no triad code touched

## base.py

`git log --oneline 887ac98^..HEAD -- noema/coordination/base.py`:

```
6f8797a feat: enhance retry mechanism with causal reflection for PES coordination
```

`noema/coordination/base.py` changed exactly once in this range, in 6f8797a,
adding the `retry_advice()` hook described above (default no-op; PES overrides
it). No further judgment rendered per the work order.

## uncommitted

`git diff --stat` at the time of this audit:

```
(empty — no output)
```

There were no uncommitted modifications in the worktree at audit time.

## test-suite

`python3 -m unittest discover tests` — last 5 lines verbatim:

```
...............................

----------------------------------------------------------------------
Ran 125 tests in 0.236s

OK
```
