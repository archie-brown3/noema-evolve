# Stage 2 — Tie retries to PES reflection

> Implements: [[tasks/0050-implement-stage2-reflection-seeded-retries]] (S)
> Blocked-by: [[tasks/0049-implement-stage1-intra-iteration-retry]]
> Spec derived from: [[PES Phase 2 Plan]] Design 4
> Signed off by the user 2026-07-09. The sanctioned "second consumer" base.py addition.

## Motivation

Stage 1's retry loop alone doesn't differentiate PES from Null — both arms
get it identically (D10). The differentiator is tying retries to PES's
reflection (Design 1, already shipped as Stage 0): **when a retry is needed,
seed it with the causal explanation reflection produces, not just the raw
error text.** This moves noema's PES closer to what LoongFlow's paper is
actually describing — planning and execution tightly coupled and iterative
(plan → attempt → see *why* it failed → attempt again informed by *why*,
not just *that*) — rather than the current "plan once, append as a static
prompt suffix" shape.

The Stage 0 comparison run revised expectations: the win came from
first-attempts + diversity, not reflection-refined lineages. The binding
constraint looks like execution fidelity, not planning knowledge. So this
is **low-cost icing on top of Stage 1** — the reflection text is already
sitting there in `self._plans[parent.id]["reflection"]` when a retry fires.
Stage 1 carries most of the value; Stage 2 is the cheap differentiator.

## Design

**Nature:** PES-only differentiator. Depends on Stage 1's retry loop
existing. Requires the approved `base.py` interface addition.

### Interface addition (`noema/coordination/base.py`)

Add a **non-abstract** default method to `CoordinationModule`. The addition
is the sanctioned "second consumer" case (CLAUDE.md: "sanctioned when a
second mechanism needs it"). Not a new `@abstractmethod` — `NullCoordination`,
`HiFo`, and `s1` inherit the no-op unchanged:

```python
async def retry_advice(
    self, ctx: GenerationContext, error_text: str, attempt: int
) -> str:
    """Text to append to a retry's mutation prompt (default: none).
    Called by the controller's retry loop after a failed attempt, before
    re-issuing the mutation call. "" means the retry uses raw error only.
    """
    return ""
```

`base.py` diff is limited to this one method — no change to existing abstract
signatures or `NullCoordination`.

### PES override (`noema/coordination/pes/module.py`)

```python
async def retry_advice(self, ctx, error_text, attempt) -> str:
    if ctx.parent is None:
        return ""
    prior = self._plans.get(ctx.parent.id)
    reflection = prior.get("reflection") if prior else None
    if not reflection:
        return ""
    return (
        "\n# Reflection on the lineage's last failure\n"
        f"{reflection}\n"
        "Use this causal explanation to guide the corrected mutation."
    )
```

`advise()`, `_reflect()`, `report_result()`, and the prompt templates are
unchanged — this is purely the retry-seeding path.

### Controller integration (`noema/controller.py`)

Stage 1 builds the raw-error suffix; Stage 2 additionally calls the hook
and concatenates:

```
retry_suffix = build_raw_error_suffix(error_text, attempt)
reflection_suffix = await self.coordination.retry_advice(ctx, error_text, attempt)
full_suffix = retry_suffix + reflection_suffix
```

The call is arm-agnostic at the controller; the difference is in the
module's return value only:

| arm | `retry_suffix` | `reflection_suffix` | `full_suffix` |
|---|---|---|---|
| Null | raw error text | `""` (inherited no-op) | raw error only |
| PES | raw error text | reflection text (or `""`) | raw error + causal explanation |

**The controlled variable holds:** both arms run the identical retry loop;
only the coordination module's return differs.

### Why reflection is available at retry time (not a race)

Reflection for a lineage is set at `on_generation_end` (`_reflect`,
`module.py:375`), keyed by the *parent's* id. A lineage's next mutation
is always after that tick (round-robin scheduling: `island = iteration %
num_islands`, tick fires after every full round). When PES retries a
mutation of parent P, `self._plans[P.id]["reflection"]` already holds P's
own prior reflection. No new deferral/ordering hazard introduced.

## Docstring update

Module docstring deviation #2 (`module.py:25`) currently reads:

> The plan is a prompt *suffix* after openevolve's mutation instructions, not
> the executor's primary directive. Biggest fidelity gap — flagged in the fit
> assessment; accepted for now (closing it is Stage 2 of the Phase 2 roadmap).

Update to note the gap is now closed: the plan informs a retry seeded by
causal reflection, making planning and execution iterative rather than
"plan once, append as static suffix."

## Implementation constraints

- `base.py` diff: exactly one non-abstract default method added. No change
  to existing `@abstractmethod` signatures, `NullCoordination`, or any other
  method.
- `pes/module.py`: only `retry_advice` override added. `advise()`,
  `_reflect()`, `report_result()`, and prompt templates unchanged.
- `controller.py`: minimal — one call to `retry_advice` + concatenation
  inside the Stage 1 retry loop.
- Diff under 200 lines (CLAUDE.md).

## Test plan

### `tests/test_noema_coordination_base.py`

- `retry_advice` default returns `""`
- Signature is correct (`ctx`, `error_text`, `attempt`)
- Method is awaitable (async contract)
- `NullCoordination` inherits the no-op unchanged

### `tests/test_noema_pes.py`

- PES `retry_advice` returns reflection text when `self._plans[parent.id]`
  has a `"reflection"` key with non-empty text
- Returns `""` when `ctx.parent is None`
- Returns `""` when no reflection stored yet (fresh lineage, no entry in
  `_plans`)
- `NullCoordination.retry_advice` returns `""` (regression guard against
  confounding — a Null retry must NOT carry reflection)
- Reflection text is framed with the `"# Reflection on the lineage's last
  failure"` header

### `tests/test_noema_controller.py`

- End-to-end: PES retry prompt contains reflection text; Null retry prompt
  does not, given the same error text (drive both arms through the same
  controller retry loop)
- Confirms the controlled variable holds — difference is in the module, not
  the controller

### `tests/test_noema_prompts.py`

- Lock the reflection-suffixed retry prompt variant (structure assertion —
  substrings `"# Reflection on the lineage's last failure"` and
  `"Use this causal explanation"` present when PES retries)

## Verification

`loop/guardrails/verify.sh` green before and after commit.
All guarantee-triad tests (controller, prompts, coordination base) extended
in the same commit per CLAUDE.md law.

## Related

- [[PES Phase 2 Plan]] — Design 4, the "actual candidate edge"
- [[tasks/0049-implement-stage1-intra-iteration-retry]] — hard dependency
- [[LoongFlow Fit Assessment]] — deviation #2 (closed by this task)
- [[PES Stage 0 vs Null — Circle Packing Comparison — 2026-07-08]] — revised
  expectations: Stage 1 carries the value, Stage 2 is icing
- [[Noema Architecture]] — controlled-variable holds across arms
