---
title: Population substrates
updated: 2026-07-26T00:00:00Z
tags: [noema, substrates, population]
---

# Population substrates

This package owns population storage, neutral store and policy contracts, and
runtime construction. Selection policies remain in [`noema.selection`](../selection/README.md).

See the [noema package guide](../README.md) for the complete package map.

## Package map

- [`base.py`](./base.py) defines the store-neutral contracts and `SubstrateRuntime`.
- [`registry.py`](./registry.py) composes configured stores and selection policies.
- [`islands.py`](./islands.py) implements the OpenEvolve islands and MAP-Elites store.
- [`tree.py`](./tree.py) implements persistent lineage storage.
- [`cvt.py`](./cvt.py) implements the CVT-MAP-Elites archive.
- [`cvt_behavior.py`](./cvt_behavior.py) extracts deterministic behaviour features.
- [`database.py`](./database.py) isolates the OpenEvolve database adapter.

The controller imports only the runtime builder. Stores do not select their own
parents unless their neutral contract explicitly exposes a native policy.
