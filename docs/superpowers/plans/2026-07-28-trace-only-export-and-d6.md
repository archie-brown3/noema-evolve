# Trace-only EvoReplay export and D6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the committed Phase C trace records exportable and use them to run D6 timing/rescue diagnostics.

**Architecture:** Preserve checkpoint export unchanged. Add one trace-only fallback that reconstructs program records from accepted attempts plus evolution metadata; D6 then reads the immutable trace sidecars to classify coordination timing and whether later accepted programs exceeded the pre-event best score.

**Tech Stack:** Python 3.10+, JSONL, pytest, existing Noema exporter, EvoReplay refined layout.

## Global Constraints

- Do not modify Phase C experiment artifacts.
- Keep the checkpoint path behaviour unchanged.
- Treat Phase C n=1 cells as descriptive engineering evidence only.
- Do not call an LLM or launch a live run.

---

### Task 1: Trace-only exporter fallback

**Files:**
- Modify: `tests/test_noema_export_evoreplay.py`
- Modify: `noema/export_evoreplay.py`

**Interfaces:**
- Consumes: `attempt_trace.jsonl` accepted records with `parent` and `candidate`.
- Consumes: `evolution_trace.jsonl` records keyed by `child_id`.
- Produces: existing `export_run(run_dir, output_dir) -> Path` outputs.

- [ ] **Step 1: Write the failing test**

Create a run with no `checkpoints/`, a config, one accepted attempt containing an
`initial` parent and `it000001` candidate, matching evolution metadata, and one
selection. Assert `export_run()` creates both program rows, code blobs, copied
attempt/selection records, and `meta.counts.checkpoints == 0`.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `uv run pytest tests/test_noema_export_evoreplay.py -k trace_only -v`

Expected: `FileNotFoundError: no checkpoints found`.

- [ ] **Step 3: Write the minimal implementation**

Add JSONL loading and a trace-program collector. For each accepted attempt, emit
its parent once and emit its candidate once, combining candidate id/code with the
matching evolution record's `child_metrics`, `parent_id`, and `generation`. Use
empty iteration/scalar collections except accepted-candidate membership rows.

- [ ] **Step 4: Run exporter tests**

Run: `uv run pytest tests/test_noema_export_evoreplay.py -v`

Expected: all tests pass.

### Task 2: Rebuild authoritative inputs and run D6

**Files:**
- Replace: `noema-analysis/data/raw/phase-c-p1/` inputs from `bb3a147`
- Regenerate: `noema-analysis/data/refined/phase-c-p1/`
- Create: `noema-analysis/outputs/phase-c-p1/d6-timing.md`

**Interfaces:**
- Consumes: all 24 trace-complete Phase C run directories extracted from
  `bb3a147`.
- Produces: per-cell plateau iteration, coordination-event timing class, and
  post-event rescue outcome.

- [ ] **Step 1: Extract the exact committed artifacts**

Use `git archive bb3a147 examples/bin_packing/runs/phase-c-p1` into a temporary
directory, then copy each complete run directory immutably into analysis raw
data. Record `bb3a14787603912f196089a7b06eb5b8a16a6659` in every manifest.

- [ ] **Step 2: Export every run and prove trace retention**

Run the exporter for all 24 raw directories. Verify each refined `meta.json`
has a positive attempt count and that copied attempt/selection records equal
their raw counterparts.

- [ ] **Step 3: Compute D6**

For each cell, calculate the last accepted improvement iteration from
`evolution_trace.jsonl`. Parse coordination events from the run log / attempt
coordination payload, classifying each as pre-plateau, at-plateau (within 10
iterations), post-plateau, or never-fired. A rescue is an accepted child with a
score above the pre-event best after the event.

- [ ] **Step 4: Report limitations and commit**

Write the per-cell table and arm-level counts, explicitly marking n=1 and known
faithfulness defects. Run the relevant test suites, then commit the exporter
fix and regenerated analysis evidence separately.
