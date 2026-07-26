---
title: noema punctuated-equilibrium coordination arm
updated: 2026-07-26T00:00:00Z
tags: [noema, coordination, punctuated-equilibrium]
---

# Punctuated-equilibrium arm

The `pe` arm periodically proposes paradigm shifts and program variants.

See the [coordination package guide](../README.md) for the shared module contract.

## Public surface

[`noema.coordination.pe`](./__init__.py) exports [`PunctuatedEquilibriumModule`](./module.py).
[`prompts.py`](./prompts.py) contains the two proposal prompt builders.

## Composition

The registry constructs the module for `coordination.module: "pe"`.
`advise` returns empty advice during ordinary mutations.
At configured generation ticks, `on_generation_end` clusters the global elites.
The module then returns proposed programs in an `Intervention`.
The controller evaluates and inserts each valid proposal.

## Guarantee responsibility

The controller supplies all LLM clients and the arm-specific random stream.
Both optional model tiers charge the coordination account.
Seeded clustering and single-thread limits keep cluster selection reproducible.
The host retains evaluation order and program insertion.

The implementation records its [LEVI provenance and noema adaptations](./module.py).
