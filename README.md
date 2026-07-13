# noema

Controlled ablation of coordination mechanisms in LLM-driven evolutionary search.

noema implements an independent evolutionary controller while reusing selected
OpenEvolve components (evaluator, program database, prompt sampler) through
isolated adapters. The study compares coordination **arms** by changing only
`coordination.module`, while keeping seeds, prompts, budget, and loop behavior fixed.

## OpenEvolve library context

noema uses OpenEvolve as an installed **library dependency**, not as vendored code
or a local submodule in this repository.

- Borrowed from OpenEvolve: evaluator, program database, prompt sampler, and related
  utility modules accessed through `noema/substrate/` adapters.
- Not borrowed: OpenEvolve's top-level iteration orchestration. noema runs its own
  controller loop in `noema/controller.py`.
- Dependency pin: `openevolve @ git+https://github.com/codelion/openevolve@80945ed`
  (defined in `pyproject.toml`).

This separation keeps the study variable controlled: coordination changes are
isolated to noema modules while substrate behavior remains pinned and auditable.

## What this repository provides

- A standalone controller loop in `noema/controller.py`
- Pluggable coordination modules behind `noema/coordination/base.py`
- Shared token metering for all LLM calls via `noema/budget/`
- Checkpoint/resume support including controller, DB, ledger, and coordination state
- Tests that protect prompt identity, metering integrity, and determinism

Design and audit details are documented in [`PLAN.md`](PLAN.md).

## Install

Python 3.10+ is required.

```bash
pip install -e ".[dev]"
```

This installs `noema` plus the pinned OpenEvolve library dependency from commit `80945ed`.

## Minimal run example

```python
import asyncio
from noema import NoemaConfig, NoemaController

config = NoemaConfig.from_yaml("experiment.yaml")
controller = NoemaController(
    config=config,
    evaluation_file="evaluator.py",  # defines evaluate(program_path)
    initial_program_code=open("initial.py").read(),
    output_dir="runs/arm_off",
)
best = asyncio.run(controller.run())
```

### Arm selection (single controlled variable)

```yaml
budget:
  total_tokens: 1000000
coordination:
  module: "null"            # OFF / brute-force baseline
  # module: "hifo"
  # module: "pes-faithful"
substrate:
  kind: "islands"           # "tree" is specified but not yet implemented
selection:
  policy: "substrate_default"   # or "stock_openevolve" / "boltzmann"
```

Use identical config outside `coordination.module` when comparing arms.

## Ablation axes — what exists, what is planned

The study varies coordination **mechanisms** against population **substrates** at
equal token budget. Selection policy is a third axis, decoupled from topology, so
any policy can be paired with any store.

### Mechanisms — `coordination.module`

| mechanism | status | what it is |
|---|---|---|
| `null` | **implemented** | coordination-OFF. The control arm. |
| `hifo` | **implemented — not valid** | HiFo-Prompt insight pool + evolutionary navigator. Fidelity defects found after the transplant; excluded from reported results pending remediation. |
| `pes-faithful` | **implemented** | LoongFlow plan–execute–summarize, near-verbatim recast. The reference / validity anchor. Its variant behaviour (retry trigger, executor mode, prompt variant) is set by config, not by a separate arm. |
| `bandit` | *planned* | AsymmetricUCB over the operator menu. Zero coordination LLM calls — the only free mechanism on the axis. |
| `punctuated` | *planned* | Punctuated equilibrium: hill-climb between periodic regime changes, rather than reasoning on every mutation. |

### Substrates — `substrate.kind`

| substrate | status | what it is |
|---|---|---|
| `islands` | **implemented** | islands + MAP-Elites. Migration-mixed fronts, broken lineages. |
| `tree` | *planned* | global tree + UCT. Deep persistent lineages. Raises explicitly until built. |

### Selection policies — `selection.policy`

| policy | status | what it is |
|---|---|---|
| `substrate_default` | **implemented** | the store's native policy. |
| `stock_openevolve` | **implemented** | OpenEvolve's sampling, unchanged. |
| `boltzmann` | **implemented** | Boltzmann sampling with adaptive temperature and optional stagnation detection. |

## Outputs and resume

- Checkpoints: `output_dir/checkpoints/`
- LLM call log: `output_dir/llm_calls.jsonl`

Resume by loading a checkpoint before running:

```python
controller.load_checkpoint("runs/arm_off/checkpoints/<checkpoint_name>")
best = asyncio.run(controller.run())
```

## Guarantees enforced by tests

- **Prompt identity across arms**: shared prompt prefix stays byte-identical
- **No unmetered LLM calls**: mutation and coordination usage flows through the ledger
- **Determinism controls**: deterministic IDs and isolated coordination RNG stream

For cross-process bit-identical reruns, pin `PYTHONHASHSEED`.

## Borrowed and adapted code

- `noema/coordination/hifo/` contains code copied from
  [HiFo-Prompt](https://github.com/Challenger-XJTU/HiFo-Prompt)
- `noema/coordination/pes/` contains code adapted from
  [LoongFlow](https://github.com/baidu-baige/LoongFlow) (Apache-2.0)

Borrowed files include provenance headers; local changes are marked with `NOEMA:`.

## Repository layout

```text
noema/       framework code (controller, budget, coordination, substrate adapters)
tests/       regression tests for noema guarantees and modules
examples/    benchmark inputs (run artifacts are gitignored, not committed)
spec/        the study contract: claims, matrix, pre-registered predictions
PLAN.md      architecture and audit design notes
```

## Run tests

```bash
pytest tests/test_noema_*.py
# or
python -m unittest discover tests
```
