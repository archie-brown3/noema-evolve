# KernelBench L1/88 coordination smoke pilot

Full contract: [`spec/KERNELBENCH-P88-PILOT.md`](../../spec/KERNELBENCH-P88-PILOT.md).
Vault tickets: [0104](../../../claude-brain/tasks/0104-one-problem-kernel-writing-pilot.md)
(full scope) and [0112](../../../claude-brain/tasks/0112-kernelbench-pilot-phase-1-configs-aggregator-no-gpudocker.md)
(this slice).

## What this slice is — and is NOT

**This slice (task 0112) does not run real kernels.** It contains only:

- `config/` — the invariant base config + four arm overlays (`null`, `hifo`,
  `pes-faithful`, `bandit`), differing ONLY in `coordination.module`.
- `config_loader.py` — deep-merges base + overlay, refuses any other delta.
- `worker_protocol.py` — the schema-version-1 JSON contract a real worker
  must emit, and construction/parsing helpers.
- `executor.py` — the pluggable `KernelExecutor` interface, plus a CPU-only
  `StubExecutor` used by every test here.
- `aggregate.py` — `fast_p` computation and secondary diagnostics from
  already-confirmed run summaries.

**Not built here** (tracked in [ticket 0104](../../../claude-brain/tasks/0104-one-problem-kernel-writing-pilot.md),
blocked on a GPU + container-runtime-capable environment — this sandbox has
neither `nvidia-smi` nor `docker`, confirmed 2026-07-22):

- the disposable, network-disabled GPU container that executes untrusted
  generated CUDA — a real security boundary that must not ship unverified;
- the offline preflight (needs a real CUDA compile against the pinned seed);
- `run_arm.py` / `confirm.py`'s live-launch paths;
- any live LLM search.

No GPU, no docker, no CUDA compilation, and no network call happens anywhere
in this slice. Every test uses `StubExecutor` and fabricated
`ArmRunSummary`/`WorkerResult` data.

## Running the tests

```bash
python -m pytest tests/test_kernelbench_p88_config.py \
                  tests/test_kernelbench_p88_protocol.py \
                  tests/test_kernelbench_p88_aggregate.py
```

## Using the pieces

```python
from examples.kernelbench_coordination_smoke.config_loader import load_all_arm_configs

configs = load_all_arm_configs()  # {"null": NoemaConfig, "hifo": ..., ...}
```

```python
from examples.kernelbench_coordination_smoke.aggregate import ArmRunSummary, aggregate

report = aggregate([ArmRunSummary(arm="null", invariant_fingerprint={...}, ...), ...])
print(report.disclaimer)  # always: "Single-problem pipeline smoke test (N=1) — no comparative inference."
```

When the sandboxed worker (0104) lands, it only needs to implement
`KernelExecutor.execute(code, sha256) -> WorkerResult` — everything else in
this slice already consumes that interface.
