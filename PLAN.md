# noema: Design Document

> Reconstituted 2026-07-10; refreshed 2026-07-13 against the merged substrate
> decoupling (task 0074) and the 0070 baseline-gate result. Code cross-references
> below are the ground truth.

noema owns the top-level evolution loop and borrows OpenEvolve's evaluator,
program database, and prompt sampler as libraries. Coordination mechanisms are
pluggable modules behind one interface, so coordination-present vs absent is a
single controlled variable. Every LLM call is metered against a shared token
budget.

## 1. OpenEvolve audit — what we borrow, what we don't

We borrow: evaluator, program database (SQLite), prompt sampler (template
manager), `code_utils` (diff *parsing*). Borrowed store: `openevolve`, pinned to
commit `80945ed` (tag `v0.2.27`).

Diff **application** is no longer borrowed. `openevolve.apply_diff` requires a
byte-exact match on the SEARCH block, so an LLM that re-indents the snippet
produces a silent no-op — the child is byte-identical to its parent and the
iteration is wasted with no error. `noema/diff.py::apply_diff_lenient`
is indentation-aware and replaces it at the single call site in
`controller.py`.

We do **not** borrow: the top-level iteration loop (`iteration.py`,
`process_parallel.py`), the novelty system, LLM feedback evaluation. The loop
is a fully independent reimplementation in `noema/controller.py`.

### 1.2 Database — generation bookkeeping

`SubstrateDatabase` (`noema/database.py`) wraps OpenEvolve's program
database. The external controller must drive generation ticks —
`end_generation()` increments the generation counter, triggers migration when
due, and the DB itself owns no timer or epoch concept.

### 1.3 Evaluator

`noema/evaluator.py` configures the evaluator: LLM feedback is
rejected (site #3 is structurally dead, not just configured off), cascade
evaluation is off, novelty features are rejected.

### 1.4 Substrate abstraction (rewritten by task 0074)

The islands-specific API described here originally (`sample_from_island(island, n)`)
is **superseded**. Task 0074 decoupled parent selection and population structure
from the islands model so further substrates (tree/UCT, CVT) can be added without
touching the loop:

- `noema/base.py` — the store-neutral interface: `select(...)`,
  `PopulationSnapshot`, scopes instead of islands.
- `noema/islands.py` — the islands + MAP-Elites store (the incumbent).
- `noema/registry.py` — substrate selection by config.
- `noema/selection/` — selection policies (`stock_openevolve`,
  `boltzmann`), separable from the store itself.

`SubstrateDatabase` (`noema/database.py`) remains the openevolve adapter
underneath. Generation bookkeeping is still driven by the controller (§1.2).

**TreeStore is not built** (task 0037). Its fidelity spec exists as intentionally
failing tests (`tests/test_noema_tree_store_fidelity_spec.py`).

### 1.5 Prompt assembly

All arms share identical prompts except for the coordination block (`noema/
substrate/prompts.py`). Two rules:
1. Template stochasticity forced OFF (openevolve defaults it ON)
2. Coordination advice injected as a delimited suffix — shared prefix is
   byte-identical across arms, verifiable by diffing logged prompts.

## 2. Coordination mechanisms

Pluggable behind `CoordinationModule` (`noema/coordination/base.py`). Four hooks:
`sampling_request(ctx)` — synchronous, declarative selection hints issued
*before* parent/operator selection (added by task 0074; default no-op);
`advise(ctx)` — per-mutation prompt guidance before the LLM call;
`report_result(ctx, child, attribution)` — records outcome; `on_generation_end
(ctx)` — generation-level processing.

Arms (registry keys, `noema/coordination/__init__.py`):
- `null` — coordination-OFF, the control.
- `hifo` — HiFo-Prompt's insight pool + navigator. **Implemented but NOT valid**
  (task 0072): three fidelity defects — insight extraction is fed truncated code
  because `changes_description` is never populated; the navigator cannot reach its
  exploitation regime (fitness history updates per generation tick, but the
  navigator is consulted per offspring, so its stagnation counter saturates even
  under steady improvement); and its regime has no operator scheduler to govern.
  **Excluded from results pending remediation.**
- `pes-custom` — the LoongFlow-derived planner, noema's refinement (the
  contribution).
- `pes-faithful` — the near-verbatim LoongFlow recast (reference arm / validity
  anchor, explicitly *not* the contribution).
- `bandit` — **specified, not built** (task 0073). AsymmetricUCB over the EoH
  operator menu; would be the first consumer of the `sampling_request` seam.

`pes` is a deprecated alias for `pes-custom` (task 0066 split).

### 2.2 HiFo / PES — deviations from released code

Both modules carry a deviations list in their `module.py` docstrings:
- HiFo: in-process credit assignment (released code lost feedback to joblib
  subprocess copies), maximized-fitness convention. See the fidelity defects above
  — the transplant is documented, but not currently faithful.
- PES: adapted from LoongFlow (Apache-2.0), async advise + delayed reflection.

## 3. Implementation

### 3.1 CoordinationModule interface

`noema/coordination/base.py` — `Advice`, `GenerationContext`, `SelectionContext`,
`SamplingRequest`, `CoordinationModule` (ABC), `build_coordination_module(module_name)`.
**Never modify without asking.**

Task 0074 is the one sanctioned exception to date: it redesigned `GenerationContext`
(`island` → `scope_id`, `island_fitnesses` → local/global `PopulationSnapshot`,
lists → tuples) and removed `Advice.sampling_hint` in favour of the pre-selection
`sampling_request` seam. Approved by the user; the law stands for everything else.

### 3.2 Token budget

`noema/budget/ledger.py` — `TokenLedger` with per-account accounting.
`charge()` never raises — tokens are spent and the caller checks `remaining()`
before making calls. Two accounts: `mutation` and `coordination`.

### 3.3 Controller loop

`noema/controller.py` — single-process, strictly sequential:
sample → advise → prompt → mutate → parse → evaluate → add → report → tick →
checkpoint.

### 3.4 Risks

| # | Risk | Mitigation |
|---|------|------------|
| 1 | openevolve defaults stochasticity ON | `NoemaConfig.__post_init__` rejects it |
| 2 | Unmetered LLM calls | DB rejects novelty features; evaluator rejects LLM feedback |
| 3 | Prompt config defaults differ from openevolve | `NoemaConfig` enforces stochasticity OFF, cascade OFF, no novelty |
| 4 | Coordination module RNG perturbing shared stream | Dedicated `coordination_rng` seeded from `random_seed + 1` |
| 5 | checkpoint resume losing coordination/db state | `save_checkpoint` / `load_checkpoint` round-trip ledger + RNG + DB |
| 6 | openevolve upgrade silently breaking noema | Adaptors in `substrate/` isolate all openevolve imports |

## 4. Task list

_Numbered tasks from the original PLAN.md, preserved for code cross-references.
Status reflects current (2026-07-13) state._

1. **(done)** Extract noema as standalone repo (task 0023)
2. **(done)** Implement null (coordination-OFF) controller loop
3. **(done)** Implement BudgetedLLM + TokenLedger
4. **(done)** Implement HiFo coordination module
5. **(done)** Implement PES coordination module
6. **(done)** Fix single-island bug (task 0032)
7. **(done)** Implement intra-iteration retry (task 0049)
8. **(done)** Implement Stage 2 reflection-seeded retries (task 0050)
9. **(done)** Role-structured benchmark layout — F_imm / F_mut boundary (task 0034)
10. **(done)** Frozen run config with hash (task 0041)
11. **(done)** `verify-run.sh` script (task 0038)
12. **(done — task 0074)** Pre-selection substrate request seam. Coordination
    modules may synchronously return a neutral `SamplingRequest` before parent
    selection. `Advice.sampling_hint` was removed because advice is produced too
    late to influence selection. Operator routing remains task 0073.
13. **(done — task 0027)** Substrate-level EoH operator menu (e1, e2, m1, m2, m3;
    i1 excluded — population-init only in EoH, no per-iteration equivalent).
    Uniform-random-per-iteration selection, strictly opt-in via
    `NoemaConfig.mutation_operators` (`None` default = zero behavior change).[^1]
    Adaptive/reward-based operator selection is future work, separate from this
    task (see task 0018 / bandit arm).
14. **(pending)** Port bin-packing benchmark (task 0036) — the benchmark source is
    on `main` (`examples/bin_packing/`); the port task is the noema-side wiring.
15. **(superseded)** s1 lineage arm (task 0035). Reconsider only as explicit
    substrate-level lineage context after TreeStore; it is not a headline arm.
16. **(pending)** Population-store seam → TreeStore (task 0037, Phase B)
17. **(pending)** Wire evolution tracer with ledger (task 0039)
18. **(pending)** PES full controller-loop test (task 0040)
19. **(done — task 0042)** Fix PES lineage loss on plan failure
20. **(pending — task 0072)** HiFo fidelity remediation. **Blocks any hifo result.**
21. **(pending — task 0073)** Bandit arm — the 5th mechanism level, and the first
    consumer of the `sampling_request` seam.
22. **(pending — task 0071)** `coordination.cadence` + a LEVI-style `punctuated`
    arm (reasoning batched every N evals rather than interleaved every mutation).
23. **(pending — task 0079)** Restore the `apply_diff_lenient` regression corpus.
    `tests/test_apply_diff.py` is committed but its fixtures never were; 7 tests
    fail. The function itself is fine (its 6 unit tests pass).

## 4.1 Current blocker (2026-07-13)

The **diff/prompt code audit is mandatory before any matrix work.** This is not a
preference — it is the branch that task 0070's own pre-registered interpretation
rules select, given the result it produced.

The 0070 baseline gate ran null / pes-custom / pes-faithful on circle_packing at
1M tokens each (seed 42, Qwen3-30B-A3B-Q8_0, enriched prompt):

| arm | best | invalid evals |
|---|---|---|
| null | **0.5555** | 65/85 = **76%** |
| pes-custom | **0.7413** | 16/57 = **28%** |
| pes-faithful | crashed at 9% of budget (context-guard bug) | — |

Two findings, in order of consequence:

1. **Enriching the mutation prompt did not fix the uncoordinated loop.** null went
   0.547 → 0.5555 (**+1.6%**) for a full million tokens and 85 evaluations, and
   still loses to a **single one-shot prompt (0.594)**. The starvation hypothesis is
   refuted. The loop itself is now the suspect, which is what the audit must settle.
2. **The same loop under coordination reaches 0.7413** — so the substrate is not
   incapable; the *uncoordinated* loop is. And pes-custom produces valid programs
   ~3× more often on an identical model and prompt. That validity gap is the most
   stable signal in the run and has no explanation yet.

A live candidate for the audit, worth checking first: the 0070 runs executed
*without* `apply_diff_lenient` (it was not on that branch). Every mutation used
openevolve's strict `apply_diff`, which silently no-ops on a re-indented SEARCH
block — producing a child byte-identical to its parent. Measuring that no-op rate
over the 0070 corpus is cheap and could account for a large share of null's 76%
invalid rate. See task 0079.

[^1]: Narrows the prior assumption that noema uses only OpenEvolve-style
    diff/rewrite mutation prompts. The legacy toggle (`diff_based_evolution`)
    remains the default path; the EoH menu is strictly opt-in, scoped to
    default/uniform selection only.

## 5. Spec documents (canonical)

Now committed to `main` under `spec/` (2026-07-13), seeded from the vault copy at
`~/claude-brain/spec/`, which remains the live working copy.

- `spec/STUDY.md` — the signed-off study spec (frozen contract)
- `spec/DELIVERABLES.md` — the live direction: claims C1–C4, the mechanism ×
  substrate matrix, pre-registered predictions
- `spec/LIVE-RUNS.md` — the live-run protocol
- `spec/LOOP-AUTONOMY.md` — proposed ticket-ready wake condition (draft)
- `spec/MLX-TRACK.md` · `spec/pes/` — MLX track, PES stage specs
