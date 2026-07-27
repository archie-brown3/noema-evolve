---
title: noema selection package
updated: 2026-07-27T00:00:00Z
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

## Store compatibility

Each policy declares a `required_capabilities` frozenset. Each store declares a
`capabilities` frozenset. `SubstrateRuntime.__init__` raises `ValueError` if the
policy requires a capability the store does not advertise — this is the only
enforcement point. There is no per-policy-per-store registration or dispatch.

### What `sampling_weights` means

Programs carry a `sample_weight` float in `program.metadata`. Boltzmann reads this
value to bias parent selection toward historically productive parents, and writes an
updated weight back when a child is accepted. The store does not compute or maintain
these weights — it only persists `program.metadata` verbatim, which all three stores
(Islands, Tree, CVT) already do through their `add()`, `state_dict()`, and
`load_state_dict()` methods.

### Compatibility table

| Policy | Islands | Tree | CVT |
|---|---|---|---|
| `StockOpenEvolveSelection` | default | — | — |
| `BoltzmannSelectionPolicy` | ✓ opt-in | ✓ opt-in | ✓ opt-in |
| `UCTSelectionPolicy` | — | default | — |
| `CVTSelectionPolicy` | — | — | default |

`—` means the policy requires capabilities the store does not expose.

### Opting in to Boltzmann

Boltzmann is the default only for Islands. To run it on Tree or CVT, name it
explicitly in your experiment YAML:

```yaml
substrate:
  kind: tree   # or cvt
selection:
  policy: boltzmann
```

The registry's `substrate_default` mapping keeps UCT for Tree and `cvt_ucb` for CVT,
so Boltzmann must be requested explicitly. The default remains the topology-aware
choice: UCT exploits the lineage tree; CVT policy exploits the behavioural archive
structure.

## Composition

[`build_substrate_runtime`](../substrates/registry.py) resolves the configured policy.
It combines the policy with an islands, tree, or CVT store.
Boltzmann composes with all three stores.
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
