---
title: noema selection package
updated: 2026-07-26T00:00:00Z
tags: [noema, selection, policy]
---

# Selection package

The `selection` package chooses parents without coupling a policy to one concrete store.

See the [noema package guide](../README.md) for the complete package map.

## Public surface

[`noema.selection`](./__init__.py) exports three policy classes:

- [`StockOpenEvolveSelection`](./stock_openevolve.py) preserves the OpenEvolve selection path.
- [`BoltzmannSelectionPolicy`](./boltzmann.py) uses adaptive-temperature sampling.
- [`UCTSelectionPolicy`](./uct.py) selects expansion nodes from a tree store.

[`CVTSelectionPolicy`](./cvt.py) is constructed directly by the substrate registry.

## Composition

[`build_substrate_runtime`](../registry.py) resolves the configured policy.
It combines the policy with an islands, tree, or CVT store.
The controller passes selection hints through `SubstrateRuntime.select`.
The runtime forwards only hints that the policy supports.
It reports accepted and rejected children back to the policy.

## Guarantee responsibility

Selection owns no LLM call and does not enforce prompt identity or metering.
Boltzmann and CVT policies store their seeded random streams.
The stock policy preserves the controller-seeded OpenEvolve stream.
Each stateful policy saves the state required for checkpoint resume.
UCT resolves equal scores by program identifier and decays exploration by metered tokens.

The [selection contract tests](../../tests/test_noema_selection_policy_interface_spec.py) verify store-policy separation.
