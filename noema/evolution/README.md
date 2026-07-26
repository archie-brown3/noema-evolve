---
title: Evolution machinery
updated: 2026-07-26T00:00:00Z
tags: [noema, evolution, mutation]
---

# Evolution machinery

This package owns the shared mutation path used by every coordination arm and
population substrate.

See the [noema package guide](../README.md) for the complete package map.

## Package map

- [`prompts.py`](./prompts.py) builds the arm-independent mutation prompt.
- [`operators.py`](./operators.py) defines the opt-in EoH mutation menu.
- [`diff.py`](./diff.py) applies indentation-tolerant SEARCH/REPLACE patches.
- [`boundary.py`](./boundary.py) preserves immutable benchmark code.
- [`evaluator.py`](./evaluator.py) constructs the ledger-safe evaluator adapter.
- [`views.py`](./views.py) exposes immutable program views to coordination code.

These modules do not own population storage, arm state, or token accounting.
