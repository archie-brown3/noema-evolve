---
title: noema bandit coordination arm
updated: 2026-07-26T00:00:00Z
tags: [noema, coordination, bandit]
---

# Bandit arm

The `bandit` arm uses AsymmetricUCB to select a mutation operator without an LLM call.

See the [coordination package guide](../README.md) for the shared module contract.

## Public surface

[`noema.coordination.bandit`](./__init__.py) exports:

- [`AsymmetricUCB`](./module.py), the operator-selection kernel.
- [`BanditModule`](./module.py), the `CoordinationModule` adapter.

## Composition

The registry constructs `BanditModule` for `coordination.module: "bandit"`.
`sampling_request` returns the selected operator as a hint.
`advise` returns empty advice, so the mutation prompt matches the control path.
`report_result` updates the selected operator after the controller evaluates the child.

The arm records outcome counts for analysis.
It uses parent-shifted fitness improvement as its reward.

## Guarantee responsibility

This arm makes no coordination LLM calls.
Its fixed menu order resolves equal UCB scores deterministically.
Its checkpoint state contains operator counts, rewards, and outcome totals.

The implementation records its [ShinkaEvolve provenance](./module.py).
