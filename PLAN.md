# noema: Design Document

> Reconstituted 2026-07-10 (original was part of the pre-extraction fork; code
> cross-references below are the ground truth for sections that existed).

noema owns the top-level evolution loop and borrows OpenEvolve's evaluator,
program database, and prompt sampler as libraries. Coordination mechanisms are
pluggable modules behind one interface, so coordination-present vs absent is a
single controlled variable. Every LLM call is metered against a shared token
budget.

## 1. OpenEvolve audit — what we borrow, what we don't

We borrow: evaluator, program database (SQLite), prompt sampler (template
manager), `code_utils` (diff parsing / apply). Borrowed store: `openevolve`,
pinned to commit `80945ed` (tag `v0.2.27`).

We do **not** borrow: the top-level iteration loop (`iteration.py`,
`process_parallel.py`), the novelty system, LLM feedback evaluation. The loop
is a fully independent reimplementation in `noema/controller.py`.

### 1.2 Database — generation bookkeeping

`SubstrateDatabase` (`noema/substrate/database.py`) wraps OpenEvolve's program
database. The external controller must drive generation ticks —
`end_generation()` increments the generation counter, triggers migration when
due, and the DB itself owns no timer or epoch concept.

### 1.3 Evaluator

`noema/substrate/evaluator.py` configures the evaluator: LLM feedback is
rejected (site #3 is structurally dead, not just configured off), cascade
evaluation is off, novelty features are rejected.

### 1.4 Substrate database

`SubstrateDatabase` responsibilities:
- `sample_from_island(island, n)` — returns parent + inspirations list
- `add(program, iteration, target_island)` — inserts with correct island
- `top_programs(n, island)` / `best_program()` — ranked queries
- `save/load` — checkpoint round-trip
- Generation bookkeeping the external controller must drive (§1.2)

### 1.5 Prompt assembly

All arms share identical prompts except for the coordination block (`noema/
substrate/prompts.py`). Two rules:
1. Template stochasticity forced OFF (openevolve defaults it ON)
2. Coordination advice injected as a delimited suffix — shared prefix is
   byte-identical across arms, verifiable by diffing logged prompts.

## 2. Coordination mechanisms

Pluggable behind `CoordinationModule` (`noema/coordination/base.py`). Three
hooks: `advise(ctx)` — per-mutation advice before the LLM call;
`report_result(ctx, child, attribution)` — records outcome; `on_generation_end
(ctx)` — generation-level processing.

Arms: `null` (coordination-OFF, identical to brute-force OpenEvolve), `hifo`
(HiFo-Prompt's insight pool + navigator), `pes` (LoongFlow PES planner).

### 2.2 HiFo / PES — deviations from released code

Both modules carry a deviations list in their `module.py` docstrings:
- HiFo: in-process credit assignment (released code lost feedback to joblib
  subprocess copies), maximized-fitness convention.
- PES: adapted from LoongFlow (Apache-2.0), async advise + delayed reflection.

## 3. Implementation

### 3.1 CoordinationModule interface

`noema/coordination/base.py` — `Advice`, `GenerationContext`, `CoordinationModule`
(ABC), `build_coordination_module(module_name)`. Never modify without asking.

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
Status reflects current (2026-07-10) state._

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
14. **(pending)** Port bin-packing benchmark (task 0036)
15. **(superseded)** s1 lineage arm (task 0035). Reconsider only as explicit
    substrate-level lineage context after TreeStore; it is not a headline arm.
16. **(pending)** Population-store seam → TreeStore (task 0037, Phase B)
17. **(pending)** Wire evolution tracer with ledger (task 0039)
18. **(pending)** PES full controller-loop test (task 0040)
19. **(done — task 0042)** Fix PES lineage loss on plan failure

[^1]: Narrows the prior assumption that noema uses only OpenEvolve-style
    diff/rewrite mutation prompts. The legacy toggle (`diff_based_evolution`)
    remains the default path; the EoH menu is strictly opt-in, scoped to
    default/uniform selection only.

## 5. Spec documents (canonical)

- `spec/STUDY.md` — the signed-off study spec
- `spec/LIVE-RUNS.md` — the live-run protocol
- `spec/DELIVERABLES.md` — STUDY v2 draft (awaiting user sign-off)
- `spec/LOOP-AUTONOMY.md` — proposed ticket-ready wake condition (draft)
