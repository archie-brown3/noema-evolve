---
title: noema Python package
updated: 2026-07-26T00:00:00Z
tags: [noema, architecture, api]
---

# noema Python package

The `noema` package runs evolutionary search with separate coordination, selection, and token-metering components.

See the [repository overview](../README.md) for installation, configuration, and run commands.

## Public surface

[`noema/__init__.py`](./__init__.py) exports the supported top-level API:

- [`NoemaConfig`](./config.py) loads and validates experiment configuration.
- [`NoemaController`](./controller.py) hosts the shared evolution loop for
  in-process mutation runs.
- [`TokenLedger` and `BudgetedLLM`](./budget/README.md) enforce token metering.
- [`CoordinationModule` and `build_coordination_module`](./coordination/README.md) define and construct coordination arms.
- `Advice`, `GenerationContext`, and `NullCoordination` support coordination implementations.
- `BudgetExhausted` and `CallRecord` expose budget state and call records.

## Package map

- [`agenthost/`](./agenthost/) optionally hosts the shared iteration loop through
  nested coding CLIs, and owns the `noema` console script: the configure walk and
  the live run monitor (see the [repository overview](../README.md#agent-host-cli-noema)).
- [`budget/`](./budget/README.md) meters LLM calls and records token use.
- [`coordination/`](./coordination/README.md) defines the arm interface and registry.
- [`evolution/`](./evolution/README.md) owns the shared mutation and evaluation path.
- [`selection/`](./selection/README.md) selects parents through store-neutral policies.
- [`substrates/`](./substrates/README.md) owns population stores and runtime composition.

## Composition

The controller and optional agent host build one shared ledger and one substrate runtime.
They ask the selection policy for parents before each mutation.
They then ask the coordination module for structured prompt advice.
In-process LLM requests go through a metered client; nested coding-CLI mutation
calls are transport calls whose token usage is unavailable to the ledger.
Finally, they report the evaluated child to the selection and coordination components.

## Guarantee ownership

- The [`budget`](./budget/README.md) package enforces metering integrity.
- The [`coordination`](./coordination/README.md) interface confines arm-specific inputs and receives a separate random stream.
- The shared iteration runner applies deterministic identifiers and ordering.
- The controller owns checkpoint state.
- The [`selection`](./selection/README.md) policies preserve and restore their deterministic state.

The [test suite](../tests/) checks prompt identity, metering integrity, and determinism.
