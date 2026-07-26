---
title: noema HiFo coordination arm
updated: 2026-07-26T00:00:00Z
tags: [noema, coordination, hifo]
---

# HiFo arm

The `hifo` arm combines an insight pool with an evolutionary navigator.

See the [coordination package guide](../README.md) for the shared module contract.

## Public surface

[`noema.coordination.hifo`](./__init__.py) exports:

- [`HiFoPromptModule`](./module.py), the `CoordinationModule` adapter.
- [`InsightPool`](./insight_pool.py), the insight store and credit tracker.
- [`EvolutionaryNavigator`](./evolutionary_navigator.py), the search-regime selector.

## Composition

The registry constructs `HiFoPromptModule` for `coordination.module: "hifo"`.
`advise` selects insights and adds operator-specific guidance.
`report_result` assigns credit after the controller evaluates the child.
`on_generation_end` can extract new insights from top programs.

Only insight extraction calls the coordination LLM.
The controller supplies that metered client and the arm-specific random stream.

## Guarantee responsibility

The module returns prompt content through `Advice`.
It does not create an LLM client or a random generator.
Its checkpoint state includes the insight pool, navigator, and extraction cadence.

The package records its [HiFo-Prompt provenance and deviations](./module.py).
