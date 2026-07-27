---
title: noema coordination package
updated: 2026-07-26T00:00:00Z
tags: [noema, coordination, architecture]
---

# Coordination package

The `coordination` package defines one host interface for all coordination arms.

See the [noema package guide](../README.md) for the complete package map.

## Public surface

[`base.py`](./base.py) defines `CoordinationModule` and its data contracts.
These contracts include `GenerationContext`, `SelectionContext`, `SamplingRequest`, `Advice`, `Outcome`, and `Intervention`.
`NullCoordination` implements the coordination-off control.

[`noema.coordination`](./__init__.py) exports those contracts, `MODULE_REGISTRY`, and `build_coordination_module`.
The builder constructs an arm from `NoemaConfig.coordination.module`.

## Arms

- [`bandit/`](./bandit/README.md) selects mutation operators without coordination LLM calls.
- [`hifo/`](./hifo/README.md) supplies insight-pool and navigator guidance.
- [`pe/`](./pe/README.md) proposes periodic paradigm shifts and variants.
- [`pes/`](./pes/README.md) plans mutations and reflects on their outcomes.

The registry also provides the `null` control arm.

## Model escalation (task 0107)

[`escalation.py`](./escalation.py) is an arm-agnostic modifier, not an arm.
An `EscalationPolicy` routes a mutation generation to a stronger model for a
fixed burst when a trigger fires, then cools down before it can re-trigger.
Five triggers are configurable (`plateau`, `invalidity`, `budget_fraction`,
`diversity`, and `random` — the last reproduces OpenEvolve's weighted-model
coin flip as a study baseline).

The policy is owned and applied by the **controller**, which builds an
`EscalationContext` from state it already holds and sets `Advice.model` after
`advise()`. So escalation composes with any arm — including `null` — with no
arm change. It lives on the mutation seat only; the coordination seat is
untouched, preserving the single-model controlled-ablation basis. Enable it via
`NoemaConfig.coordination.escalation` (an `EscalationConfig`); `None` is off and
byte-identical to today.

## Composition

The controller asks `sampling_request` for pre-selection hints.
It asks `advise` for structured prompt content before each mutation.
It calls `report_result` after the mutation outcome is known.
It calls `on_generation_end` at each generation tick.

The controller supplies the module with a metered LLM client and a separate random stream.

## Guarantee responsibility

The interface confines arm-specific prompt changes to structured advice.
The controller applies those changes and preserves the common prompt path.
The `pes-faithful` arm declares its directive-mode prompt exception in its package guide.
Injected LLM and random handles keep metering and random state under host control.

The [coordination tests](../../tests/test_noema_coordination_base.py) verify the shared contract.
