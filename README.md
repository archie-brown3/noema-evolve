# noema

Controlled ablation of coordination mechanisms in LLM-driven evolutionary search.
Design document: [PLAN.md](PLAN.md) at the repository root.

noema owns the top-level evolution loop ([`controller.py`](controller.py)) and borrows
OpenEvolve's evaluator, program database, and prompt sampler as libraries via thin
adapters ([`substrate/`](substrate/)) — the only package that touches openevolve
internals. Coordination mechanisms are pluggable modules behind one interface
([`coordination/base.py`](coordination/base.py)), so coordination-present vs
coordination-absent is a single controlled variable. Every LLM call — mutation and
coordination alike — is metered against a shared token budget ([`budget/`](budget/)).



## Requirements 
This project uses [Openevolve](https://github.com/algorithmicsuperintelligence/openevolve) 0.3.0 as a library
```
pip install requirements.txt
# or 
pip install openevolve@0.3.0
```



## Running an arm

```python
import asyncio
from noema import NoemaConfig, NoemaController

config = NoemaConfig.from_yaml("experiment.yaml")   # or NoemaConfig(...)
controller = NoemaController(
    config=config,
    evaluation_file="evaluator.py",     # OpenEvolve-style: defines evaluate(program_path)
    initial_program_code=open("initial.py").read(),
    output_dir="runs/arm_off",
)
best = asyncio.run(controller.run())
```

The arm is selected by config alone — everything else (templates, seeds, budget,
loop) is identical:

```yaml
budget:
  total_tokens: 1000000        # one shared pool; runs are compared at equal spend
coordination:
  module: "null"               # the coordination-OFF (brute-force) arm
  # module: "hifo"             # the HiFo-Prompt arm
  # module: "pes"              # the LoongFlow PES planner arm
  # params: {tips_per_prompt: 3, extraction_probability: 0.8}
```

## Population substrate and parent selection

Population topology and parent selection are independent configuration axes. The
substrate owns population storage, topology, insertion, and generation cadence;
the selection policy chooses parents and inspirations. A coordination arm does
not choose either one, so every arm in an ablation can be run against the same
configured substrate and policy.

Existing configurations need no changes. Omitting both blocks is equivalent to:

```yaml
substrate:
  kind: islands

selection:
  policy: substrate_default  # resolves to stock_openevolve for islands
```

This default performs one atomic delegation to OpenEvolve's stock island sampler,
preserving its parent/inspiration behavior and global Python RNG stream.

To use the LoongFlow-compatible Boltzmann policy with the islands store:

```yaml
random_seed: 42

substrate:
  kind: islands
  # Optional. Defaults to database.num_islands for the islands substrate.
  # This controls generation-tick and migration cadence, not population size.
  # steps_per_generation: 4

selection:
  policy: boltzmann
  seed: 45                        # optional; defaults to random_seed + 3
  boltzmann_temperature: 1.0      # must be > 0
  boltzmann_exploration_rate: 0.2 # probability in [0, 1]
  stagnation_detection_enabled: false
  stagnation_mode: released       # exact LoongFlow 0.0.1 branch behavior
```

Boltzmann selection adapts temperature from population diversity, optionally
raises exploration after recent-score stagnation, and incorporates inherited
`sample_weight` values. Inspirations remain uniform samples without replacement.
Policy RNG, recent-score history, and sampling-weight state survive checkpoint
resume. The implementation intentionally preserves two released LoongFlow 0.0.1
quirks—the elite ID/object comparison and the unreachable ×4 stagnation branch—so
the policy remains a scientifically traceable fidelity anchor.

### Configuration fields

| Field | Default | Meaning |
|---|---:|---|
| `substrate.kind` | `islands` | Population-store implementation. `tree` is reserved for the forthcoming TreeStore and currently fails clearly at controller construction. |
| `substrate.steps_per_generation` | unset | Optional cadence override. Islands otherwise use `database.num_islands`. Keep this identical across coordination arms. |
| `selection.policy` | `substrate_default` | Native policy for the selected substrate. Current runnable choices are `stock_openevolve` and `boltzmann` with islands. `uct` is reserved for TreeStore. |
| `selection.seed` | `random_seed + 3` | Independent Boltzmann RNG seed. The stock policy continues to use OpenEvolve's existing global-Python-RNG path. |
| `selection.boltzmann_temperature` | `1.0` | Initial temperature before diversity adaptation; must be positive. |
| `selection.boltzmann_exploration_rate` | `0.2` | Base probability of uniform exploratory parent selection, from `0` to `1`. |
| `selection.stagnation_detection_enabled` | `false` | Enables LoongFlow's recent-five-score exploration adjustment. |
| `selection.stagnation_mode` | `released` | Fidelity mode for the released LoongFlow 0.0.1 stagnation logic; currently the only implemented mode. |

For controlled comparisons, change `coordination.module` independently and keep
both `substrate` and `selection` byte-identical across arms. Selection requests
made by coordination modules are logged per generation as requested, honored, or
ignored; unsupported hints do not silently alter the policy.

Checkpoints under `output_dir/checkpoints/` bundle the openevolve database with
noema state (ledger, coordination and selection-policy state, histories, RNG
streams); resume with
`controller.load_checkpoint(path)` before `run()`. Every LLM call is logged to
`output_dir/llm_calls.jsonl` with exact token usage per account.

## Guarantees the tests enforce

- **Identical prompts across arms**: template stochasticity is rejected, and
  coordination advice is injected only as a delimited suffix — the shared prompt
  prefix is byte-identical (see `tests/test_noema_prompts.py` and the two-arm
  pilot in `tests/test_noema_hifo.py`).
- **No unmetered LLM calls**: the database is constructed with novelty features
  rejected and the evaluator with LLM feedback rejected; the only API clients in
  a run belong to the ledger.
- **Determinism**: program IDs are deterministic per iteration (openevolve's
  set-based island membership makes iteration order depend on id strings), and
  the coordination module gets its own RNG stream. For bit-identical reruns
  across processes, also pin `PYTHONHASHSEED`.

## Borrowed code

`coordination/hifo/` contains code copied from
[HiFo-Prompt](https://github.com/Challenger-XJTU/HiFo-Prompt). Each borrowed file
carries a provenance header, and every local modification is marked with a
`NOEMA:` comment. Deviations from the released code (working in-process credit
assignment, maximized-fitness convention) are documented in
[`coordination/hifo/module.py`](coordination/hifo/module.py).

`coordination/pes/` contains code adapted from
[LoongFlow](https://github.com/baidu-baige/LoongFlow) (Apache-2.0). Every local
change is marked `NOEMA:` and deviations from the released code are documented in
[`coordination/pes/module.py`](coordination/pes/module.py).

## Repository layout

```
noema/    the framework (controller, budget ledger, coordination modules, substrate adapters)
PLAN.md   design document: OpenEvolve audit, architecture, task list
tests/    test suite (test_noema_*.py)
```

`openevolve` is an **installed dependency** (pinned to commit `80945ed`, tag `v0.2.27`
— "Fix bugs (#442)") declared in `pyproject.toml`. It is fetched automatically by pip
and is not present as a local directory in this repository.

## Tests

Run the full suite after installing (openevolve is fetched automatically from the
pinned commit):

```bash
pip install -e ".[dev]"          # installs noema + openevolve@80945ed
python -m unittest discover tests
# or: pytest tests/test_noema_*.py
```
