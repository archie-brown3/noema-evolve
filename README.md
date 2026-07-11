# noema

Controlled ablation of coordination mechanisms in LLM-driven evolutionary search.

noema implements an independent evolutionary controller while reusing selected
OpenEvolve components (evaluator, program database, prompt sampler) through
isolated adapters. The study compares coordination **arms** by changing only
`coordination.module`, while keeping seeds, prompts, budget, and loop behavior fixed.

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

This installs `noema` plus a pinned OpenEvolve dependency from commit `80945ed`.

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
  module: "null"   # OFF / brute-force baseline
  # module: "hifo"
  # module: "pes"
```

Use identical config outside `coordination.module` when comparing arms.

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
examples/    experiment inputs and run artifacts
PLAN.md      architecture and audit design notes
loop/        task loop and guardrail scripts
```

## Run tests

```bash
pytest tests/test_noema_*.py
# or
python -m unittest discover tests
```
