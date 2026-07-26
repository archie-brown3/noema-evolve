---
title: noema PES coordination arms
updated: 2026-07-26T00:00:00Z
tags: [noema, coordination, pes]
---

# PES arms

The `pes` package implements plan–execute–summarize coordination derived from LoongFlow.

See the [coordination package guide](../README.md) for the shared module contract.

## Public surface

[`noema.coordination.pes`](./__init__.py) exports [`PESPlannerModule`](./module.py).
The registry uses two named wrappers from [`arms.py`](./arms.py):

- `PESCustomModule` backs `coordination.module: "pes-custom"`.
- `PESFaithfulModule` backs `coordination.module: "pes-faithful"`.

The façade composes [`Planner`](./planner.py), [`Executor`](./executor.py), and [`Summarizer`](./summarizer.py).

## Composition

`advise` asks the planner for a plan and gives it to the executor.
`report_result` records the child and queues reflection work.
`on_generation_end` drains the reflection queue.
Later plans can use the stored lineage outcome.

The custom arm adds the plan as advisory prompt content.
The faithful arm uses a declared directive-mode prompt path.

## Guarantee responsibility

All planning and reflection calls use the injected coordination client.
The module stores plans and pending reflections in checkpoint state.
The registry key fixes each named arm's defining prompt and executor settings.

The implementation records its [LoongFlow provenance and deviations](./module.py).
