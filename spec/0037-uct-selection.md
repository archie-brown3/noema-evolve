# Task 0037 specification: UCT selection and tree composition

Status: implementation plan; blocked until task 0083 is committed and green  
Task: `0037-population-store-seam-treestore`  
Prerequisite: `spec/0083-tree-store.md`  
Branch: `task/0080-neutral-coordination-seam`  
Source reviewed: MCTS-AHD commit `ee9c4f424503c65a5fd2b899e6620ce86079fedb`

## 1. Purpose

Add UCT as a selection policy composed with the completed TreeStore. The store
continues to own canonical program topology and persistence. The policy owns
selection statistics and budget-dependent exploration. `SubstrateRuntime`
remains the only compositor; UCT must never import or down-cast to `TreeStore`.

This ticket makes `substrate.kind=tree + selection.policy=uct` runnable. It does
not add an MCTS-owned LLM loop or change coordination-arm behaviour.

## 2. Scientific kernel

The auditable helpers are keyword-only pure functions:

1. UCT, adapted from MCTS-AHD equation 5:
   `(Q - q_min) / (q_max - q_min) + lambda * sqrt(log(N_parent + 1) / N_child)`.
   When sibling qualities are equal, the normalized exploitation term is zero.
   Child visits are at least one before scoring.
2. Progressive widening, frozen to equation 4:
   `floor(N(node) ** alpha) >= child_count`, with default `alpha=0.5`.
3. Exploration decay, adapted from equation 7:
   `lambda0 * max(0, T - tokens_spent) / T`.
   Noema deliberately uses metered tokens rather than evaluation count. A
   non-positive budget or exhausted budget returns exactly zero.

All donor-derived code carries the pinned commit and MIT provenance. Every
Noema deviation is marked and tested.

## 3. Ownership and neutral capability

Task 0037 may add the smallest demonstrated read-only `TreeTopology` protocol to
`noema/base.py`:

- `tree_root_id() -> Optional[str]`
- `tree_children(program_id) -> Sequence[str]`

TreeStore advertises `tree_topology`; UCT requires it plus the ordinary
population and fitness capabilities. UCT receives Program payloads through
`PopulationStore.population()` and does not require a concrete class.

Canonical parent/child maps stay in TreeStore. During one synchronous selection
cycle, UCT may retain the selected path and the children observed along that
path so accepted-child backpropagation can complete without duplicating durable
topology. This pending path is cleared after accepted or rejected lifecycle
notification and is included in checkpoint state only if the host permits a
checkpoint between selection and notification.

## 4. Selection semantics

Selection starts at the trunk returned by `tree_root_id()`.

At each node:

1. If it has no children, return it for expansion.
2. If progressive widening permits another child, return the current node for
   expansion, even though it is internal.
3. Otherwise, calculate sibling UCT scores and descend to the maximum.
4. Resolve exact score ties deterministically by program ID; RNG must not affect
   this scientific trace unless a separately specified tie mode is introduced.

The returned value is neutral `Selection(parent, inspirations, source_scope,
target_scope)`. Tree scope is global, so both scopes are `None`. Inspirations
come from the store's existing elite/working-context read, excluding the parent,
with deterministic order and the requested bound.

One Noema controller iteration creates at most one accepted child. This unrolls
MCTS-AHD's expansion batch across host iterations and preserves identical host
mutation/operator behaviour across substrates.

## 5. Q/N lifecycle and backpropagation

The policy maintains JSON-safe maps `qualities[id]` and `visits[id]`.

- A newly accepted child starts with `Q=fitness(child)` and `N=1`.
- Walking the selected path upward applies equation 6:
  `Q(parent)=max(Q(child))` and `N(parent)=sum(N(child))` over known children,
  including the newly accepted child.
- Rejection adds no tree node and does not invent a visit; it clears pending
  selection state.
- Existing seed/preloaded leaves are initialized deterministically from store
  fitness when first observed.
- Internal values loaded from checkpoints are validated as finite qualities and
  positive integer visits for known IDs.

This policy state is independent of arm identity. It is substrate-level state
fixed by run configuration, not coordination feedback.

## 6. Token clock

Exploration must use `TokenLedger.spent()` before each parent selection. Add a
small optional token-observer capability at the neutral runtime boundary rather
than interpreting the existing iteration `step_size` as tokens.

The controller updates `SubstrateRuntime` from the ledger immediately before
selection. The runtime forwards the value only to policies that implement the
observer. Failed attempts and retries are therefore included by the next
selection even when they create no node. Other policies remain behaviourally
unchanged.

The current token count and configured budget are part of UCT policy checkpoint
state. Resume must produce the same next selection as an uninterrupted run.

## 7. Configuration and composition

Add and validate:

- `selection.initial_exploration` (default `0.1`, finite and non-negative)
- `selection.widening_alpha` (default `0.5`, finite and in `(0, 1]`)

`token_budget` comes from `budget.total_tokens`; policy RNG seed, if retained
for future modes, comes from `selection.seed` and is checkpointed.

Registry behaviour:

- `substrate.kind=tree` constructs `TreeStore`.
- `selection.policy=uct` constructs `UCTSelectionPolicy`.
- `selection.policy=substrate_default` resolves tree to UCT.
- Capability validation fails at composition for UCT with a non-tree store.
- Explicit incompatible combinations fail clearly; no concrete-store type
  checks appear inside UCT.

## 8. Explicit exclusions

Do not borrow MCTS-AHD's initial `i1`/`e1` population generation, `2k+2`
operator expansion, LLM/evaluator loop, `prob_rank`, `pop_greedy`, subtree
sampling, fixed depth, thought alignment, s1 path prompting, or global RNG.

The default remains one evaluated seed/trunk. Any multi-node initialization or
lineage-prompt mechanism requires a separate ticket and equal-token treatment.
The working context is consumed only through ordinary store elite/prompt reads;
it does not replace UCT's canonical tree traversal.

## 9. Atomic implementation tasks

### U1 — Red tests and neutral capability

- Replace stale toy-dict tests with real `Program` fixtures.
- Add pure-kernel hand traces.
- Add the read-only topology capability only after tests demonstrate its need.

Pass: interface tests prove UCT imports neutral contracts but not TreeStore.

### U2 — UCT policy

- Implement traversal, widening, inspirations, lifecycle, token clock, and
  JSON-safe policy state in `noema/selection/uct.py`.

Pass: hand UCT, equal-Q, widening boundary, deterministic ties, token decay,
  accepted/rejected lifecycle, and malformed-state tests are green.

### U3 — Split checkpoint determinism

- Checkpoint store state and policy/runtime state separately.
- Resume with a different constructor seed and prove the continued accepted
  parent/child trace is identical.

Pass: uninterrupted and split-run traces match exactly.

### U4 — Registry and configuration

- Register independent TreeStore and UCT constructors.
- Add strict config validation and incompatible-composition tests.

Pass: tree+UCT and tree+substrate-default construct; UCT+islands fails at the
  capability boundary; legacy islands defaults are unchanged.

### U5 — Controller and validity tests

- Add an offline fake-client run with real Programs, one child per iteration,
  deep lineage, checkpoint/resume, and ledger-driven exploration.
- Add cross-substrate prompt identity: when the selected parent and advice are
  held equal, islands and tree produce byte-identical mutation prompts.

Pass: no unmetered call occurs, no live run is launched, and prompt construction
  contains no substrate-specific branch.

### U6 — Verification and documentation

Run focused TreeStore/UCT/interface/controller suites, the current verifier
fixture suite, and relevant cross-substrate regressions. Remove each UCT
`expectedFailure` only when its unchanged behaviour is green. Record exact test
commands and update task 37's checklist.

## 10. Completion criteria

- Task 83 is committed and green before UCT production code begins.
- Published/adapted equations pass independent hand calculations.
- Progressive widening affects actual traversal, not only a helper test.
- Accepted-child backpropagation satisfies max-Q/sum-N traces.
- Token decay uses ledger tokens and reaches zero at exhaustion.
- UCT depends only on neutral capabilities and composes through the runtime.
- Tree+UCT controller and checkpoint/resume tests pass offline.
- Cross-substrate prompt identity and legacy islands regressions pass.
- No expected TreeStore/UCT failures remain and no live run is launched.
- All task-37 files are committed before completion is reported.

