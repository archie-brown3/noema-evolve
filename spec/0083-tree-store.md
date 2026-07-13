# Task 0083 specification: persistent TreeStore

Status: implementation-ready  
Task: `0083-build-persistent-tree-store`  
Follow-on: `0037-population-store-seam-treestore`  
Branch: `task/0080-neutral-coordination-seam`  
Source reviewed: MCTS-AHD commit `ee9c4f424503c65a5fd2b899e6620ce86079fedb`

## 1. Purpose

Implement a durable global program tree as a neutral `PopulationStore`. The
store must create persistent lineage structure without selecting parents or
owning any MCTS state. UCT is a separate policy delivered by task 0037.

The store has two simultaneous views:

1. a permanent tree containing every accepted `Program`; and
2. a bounded working-context projection used by global prompt/elite reads.

Evicting an item from the working context must never delete its program,
lineage, artifacts, branch membership, or contribution to population metrics.

## 2. Authoritative decisions

This specification resolves stale combined-ticket text as follows:

- The production payload is only `openevolve.database.Program`. Arbitrary dict
  payloads are not supported.
- `TreeStore` contains no `select()` method, UCT helper, policy import, visit
  count, quality value, exploration state, or RNG.
- Task 0083 does not change `noema/base.py`, `noema/config.py`,
  `noema/controller.py`, `noema/registry.py`, or coordination code.
- `native_select()` exists only because it is part of `PopulationStore`; it
  raises a clear error explaining that tree selection requires a composed
  policy.
- A tree configuration remains unavailable until task 0037 composes UCT.

## 3. Donor review and adaptation boundary

MCTS-AHD keeps a virtual root, parent/child relationships, all generated
heuristics, node Q/N state, expansion logic, and a bounded `nodes_set` inside
coupled MCTS objects. Noema borrows only:

- permanent virtual-root lineage retention; and
- the idea of a bounded working set beside the permanent tree.

Noema does not borrow MCTS-AHD's `InterfaceEC` calls, 2k+2 expansion batch,
operators, initial-population generation, `prob_rank`, `pop_greedy`, subtree
sampling, depth cap, Q/N state, global random state, or evaluation-clock decay.

## 4. Data model and invariants

### 4.1 Root, trunk, and branches

The virtual root is structural only and is never represented by a `Program`.
The first accepted parentless program is the trunk. Exactly one parentless
program is allowed.

Each direct child of the trunk starts a branch named `branch:<child-id>`.
Every descendant inherits that branch. Before the trunk has children, the only
region is `trunk:<trunk-id>`.

```
virtual root (hidden)
└── seed                         region trunk:seed
    ├── alpha                    region branch:alpha
    │   └── alpha-child          region branch:alpha
    └── beta                     region branch:beta
```

### 4.2 Private state

`TreeStore` owns private maps for programs, parents, children, branch labels,
artifacts, the trunk ID, and ordered working-set IDs. It must not mutate
`Program.metadata` to hold store bookkeeping.

Insertion is atomic. It rejects:

- non-`Program` payloads;
- empty IDs;
- duplicate IDs;
- a first program with a parent;
- a second parentless program;
- self-parenting; and
- a parent ID not already present.

An insertion failure leaves all state unchanged.

### 4.3 Global topology

- `topology == "tree_branches"`
- `target_scope(iteration) is None`
- `end_generation() is False`
- `steps_per_generation` is explicit and positive
- `feature_dimensions` is fixed store configuration
- no migration or deletion exists

## 5. Read semantics

`population(scope=None)` returns every stored program in deterministic ID order.
A trunk scope returns the trunk only. A branch scope returns every member of
that branch in deterministic ID order. Unknown scopes return an empty tuple.

Fitness uses Noema's existing `get_fitness_score` convention. Ranked output is
ordered by descending fitness and then ascending program ID.

The working context keeps at most `working_set_size` programs. It scans the
global fitness ranking and retains the first program for each distinct scalar
fitness. Therefore ties have a deterministic representative. Global
`top_programs`, `elites`, `best_program`, and snapshot prompt views use this
projection; branch-local reads use the complete branch. `population`,
`all_fitnesses`, regional summaries, and snapshot fitness distributions use the
permanent tree.

`snapshot()` exposes immutable `ProgramView` values. A global snapshot includes
all `RegionSummary` objects; a scoped snapshot includes none. Regions are the
trunk followed by direct-trunk-child branches ordered by child ID. Each summary
reports full-region size and best fitness.

## 6. Persistence schema

`state_dict()` returns a JSON-safe mapping with a schema version and:

- all dataclass fields for every `Program`;
- parent and child maps;
- branch assignments and trunk derivation;
- artifacts, including tagged base64 encoding for bytes;
- `steps_per_generation`, `working_set_size`, `feature_dimensions`, and
  `last_iteration`; and
- persisted working-set IDs.

`load_state_dict()` builds and validates replacement state before mutating the
live store. It rejects unsupported versions, malformed program payloads,
inconsistent Program IDs, incomplete lineage maps, missing parents,
self-parenting, cycles or disconnected nodes, mismatched parent/child links,
invalid branch inheritance, artifacts for unknown programs, and working-set IDs
that do not equal the deterministic recomputation.

`save(directory, iteration)` writes one deterministic JSON state file after
creating the directory. `load(directory)` reads that file and delegates to the
validated loader. A failed load leaves the existing store unchanged.

No UCT or coordination state may appear in this schema.

## 7. Files and atomic implementation tasks

### T1 — Topology kernel

- Add `noema/tree.py` with strict real-Program insertion and private lineage.
- Add a shared real-Program factory in `tests/test_noema_tree_store.py`.
- Prove invisible empty root, one trunk, stable direct-child branches, inherited
  descendant branch, non-deletion, and unchanged metadata.

Pass: focused topology tests are green and `TreeStore` has no `select()`.

### T2 — Neutral read model

- Implement all `PopulationStore` population, fitness, view, snapshot, region,
  cadence, and artifact methods.
- Implement deterministic distinct-fitness working-context pruning.

Pass: global/scoped snapshots, ties, working-context eviction, complete
population retention, and immutable `ProgramView` behaviour are asserted.

### T3 — Durable state

- Implement JSON-safe state and file save/load.
- Validate the complete topology before installing loaded state.

Pass: state and file round-trips preserve programs, topology, branches,
artifacts, cadence, working context, and ranked results; corrupt states fail.

### T4 — Specification split

- Move storage-only expectations out of the mixed red fidelity file into the
  green TreeStore suite.
- Leave five UCT-only expectations isolated and expected-failing for task 0037:
  UCT equation, widening, token decay, neutral runtime selection, and policy
  checkpoint continuation.

Pass: no storage assertion is decorated `expectedFailure`.

### T5 — Regression verification

Run:

```
python3 -m unittest tests.test_noema_tree_store -q
python3 -m unittest tests.test_noema_tree_store_fidelity_spec -q
python3 -m unittest tests.test_noema_selection_policy_interface_spec -q
bash tests/test_verify_run.sh
```

No live LLM or benchmark run is permitted.

### T6 — Documentation and hand-off

After all gates pass, update the task checklist and the durable TreeStore note
with the actual implementation and verification evidence. Link task 0037 as the
next step. Commit all task-83 repo files before reporting completion.

## 8. Completion criteria

- Every task-83 checklist item is backed by a named green test or source audit.
- TreeStore satisfies the runtime-checkable `PopulationStore` protocol.
- Storage contains no selection behaviour or policy state.
- The repository has no uncommitted task-83 files.
- Task 0037 remains the sole owner of UCT and runnable tree composition.

