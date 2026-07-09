# Stage 1 — Intra-iteration retry with error feedback

> Implements: [[tasks/0049-implement-stage1-intra-iteration-retry]] (M)
> Spec derived from: [[PES Phase 2 Plan]] Design 3, decisions D9–D10
> Signed off by the user 2026-07-09. Spec-deferral overridden — Stage-0 data sufficient.

## Motivation

The Stage 0 live run ([[PES Stage 0 vs Null — Circle Packing Comparison — 2026-07-08]])
showed PES wasting ⅔ of iterations on retryable failures: 6 no-diff responses
(format slips) and off-by-one `IndexError`s. Null's 8.1k cost-per-valid vs PES's
28.8k was driven almost entirely by dead iterations, not planning overhead.

The substrate offers no way to recover within an iteration — a failed parse or
evaluation is a dead slot, no feedback, no retry, move on to the next mutation.
This structural gap is plausibly the single biggest reason a LoongFlow-style
system would outperform plain evolutionary search: it lets one evaluation slot
absorb several attempts instead of gambling once.

## Design

**Nature:** Substrate-level, lives entirely in `noema/controller.py`. No
coordination module changes — this is the precondition for Design 4 (Stage 2),
but on its own is a fairness-preserving change that raises both arms' floor
equally per [[Noema Architecture]]'s equal-conditions requirement.

**Change site:** `NoemaController._run_iteration` (`controller.py` ~250–326).
The mutate → parse → evaluate segment is extracted into an `_attempt_mutation`
helper and driven in a bounded retry loop.

### Retryable failures and their error text

| failure | source | text fed back |
|---|---|---|
| no parseable diff / no rewrite | `_parse_response` returns `None` | `"no parseable code block found in the response"` |
| over-length | `len(child_code) > max_code_length` | `f"generated code length {len} exceeds max {max_code_length}"` |
| eval threw | `metrics` empty or has `"error"` | `child.metadata["stderr"]` (stamped at `controller.py:302`) |

### Retry prompt (raw error only, built by the controller)

The controller builds an arm-agnostic suffix from the raw error — no
coordination module internals are read:

```
# Retry After Failure
Your previous attempt failed. Error: {error_text}
Produce a corrected program. Re-output the full code.
```

### Config (on `NoemaConfig`)

| field | default | meaning |
|---|---|---|
| `retry_enabled` | `False` | When off, behavior is byte-identical to today — all existing tests pass unchanged |
| `retry_cap` | `2` | Max retries per iteration (≤3 mutation calls total) |

Config is identical across arms — a controlled-variable property.

### D9 accounting (locked by user sign-off 2026-07-09)

- **One verdict = one `max_iterations` unit**, regardless of retry count.
  Iteration counter advances once per reached-verdict. Token budget is the
  real spend metric, not iteration count.
- Retries meter on `MUTATION_ACCOUNT` normally (no new account, no
  special-casing). Each retry is a real LLM call.
- A 3-call retry iteration that still fails counts as one iteration and
  reports one `eval_failed` result — same as a single-call failure today.
- `BudgetExhausted` from a retry call propagates to `run()`'s existing
  handler — the loop stops, no partial iteration.

### Algorithm

```
for attempt in range(retry_cap + 1):
    child_code, changes_summary, response, error_text = await _attempt_mutation(...)
    if child_code is not None and not eval_failed:
        break                                    # success → normal add/report path
    if attempt < retry_cap:
        retry_suffix = build_raw_error_suffix(error_text, attempt)
        prompt = inject_retry_suffix(base_prompt, retry_suffix)
        continue
# after loop: if still failed → existing dead-iteration path
# (report_result eval_failed=True, no child added to DB)
```

On success at any attempt, the child is built/added/reported exactly as it is
today (`controller.py:275–326`) — the retry loop only changes how many mutation
calls precede a verdict.

### D10: fairness scope (locked by user sign-off 2026-07-09)

The retry loop applies identically to **every arm** (Null / HiFo / PES / s1).
*Not* applying it to Null would make any future PES-beats-Null result
meaningless — PES would have two advantages (reflection + retries) instead of
one.

### Determinism

Retry count depends on LLM output (nondeterministic per-call), but the
**structure** is deterministic: same `retry_cap`, same one-verdict-one-iteration
accounting, same fallback path. This matches the existing per-call
nondeterminism the test suite already tolerates (sampling isn't seeded
server-side).

## Why no cheap-proxy step

EvoCoder's "fast-fail validation" (§4.1.2 of the fit assessment) uses
domain-specific checks (NaN, split-leakage, column mismatches) hardcoded to
tabular ML. But the underlying *pattern* — feed the actual error back to the
same LLM and retry within the iteration before it counts as used budget — is
generic and was wrongly discarded.

circle_packing's evaluator is already cheap (~0.07–0.15s), so unlike EvoCoder's
"validation-mode small-sample" proxy checks, there's no need for a cheap-proxy
step. Retry directly against the real evaluator.

## Implementation constraints

- `noema/coordination/base.py` and all `noema/coordination/` modules — **must
  not be modified**. Stage 1 is substrate-only. The `retry_advice` hook belongs
  to Stage 2 (task 0050).
- When `retry_enabled=False`, all existing tests pass byte-identical — no
  behavior change.
- Diff under 200 lines (CLAUDE.md).

## Test plan

### `tests/test_noema_controller.py`

- `retry_enabled=False` — all existing tests pass byte-identical (regression)
- Failed parse retries up to `retry_cap`, then falls through to dead-iteration
  path; 1 iteration unit advanced, `child=None` reported
- D9: a 3-retry-failed iteration advances `max_iterations` by exactly 1
- Success on retry attempt 2 produces a normal child (add/report path unchanged)
- Retry calls charged to `MUTATION_ACCOUNT`; zero `COORDINATION_ACCOUNT` entries
- Full-cycle retry iteration end-to-end (synthetic parse failure that recovers)

### `tests/test_noema_prompts.py`

- Lock the retry-suffix prompt variant (structure assertion — substrings, not
  full string equality)

### `tests/test_noema_budget_ledger.py` / `tests/test_noema_budgeted_llm.py`

- N-retry iteration produces N mutation ledger entries, zero coordination entries
- `BudgetExhausted` propagation through a retry call

## Verification

`loop/guardrails/verify.sh` green before and after commit.
All guarantee-triad tests (controller, prompts, ledger) extended in the same
commit per CLAUDE.md law.

## Related

- [[PES Phase 2 Plan]] — Design 3, decisions D9/D10
- [[PES Stage 0 vs Null — Circle Packing Comparison — 2026-07-08]] — live evidence
- [[Noema Architecture]] — equal-conditions requirement (D10)
- [[tasks/0050-implement-stage2-reflection-seeded-retries]] — Stage 2 (depends on this)
- [[tasks/0031-investigate-pes-no-diff-rate-and-plateau]] — no-diff rate partly addressed here
