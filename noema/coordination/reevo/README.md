---
title: noema ReEvo coordination arm
updated: 2026-08-01T00:00:00Z
tags: [noema, coordination, reevo]
---

# ReEvo short-term reflection arm

The `reevo` arm contributes one donor-shaped short-term reflection hint before
an eligible mutation.

See the [coordination package guide](../README.md) for the shared module contract.

## Public surface

[`noema.coordination.reevo`](./__init__.py) exports
[`ReEvoShortTermModule`](./module.py). [`prompts.py`](./prompts.py) holds the
pinned reflector templates and code-filter helpers.

## Composition

The registry constructs the module for `coordination.module: "reevo"`.
`advise` observes the host-selected parent and a local population snapshot,
selects a strictly fitter local comparator, and makes one coordination LLM call.
The reflection is injected as a `[Reflection]` suffix on the shared mutation
prompt. `report_result` and `on_generation_end` are no-ops; the arm is
memoryless.

Only the short-term reflection call uses the coordination LLM. The controller
supplies that metered client and the arm-specific random stream (unused today).

## Guarantee responsibility

The module returns prompt content through `Advice`. It does not select parents,
cross over, evaluate, or admit programs. Checkpoint state is always empty.

The package records its [ReEvo provenance and Noema adaptations](./module.py).
Comparator selection is host-owned: native ReEvo draws two parents independently,
whereas this coordination-only arm keeps host parent selection unchanged.
