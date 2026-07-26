---
title: noema budget package
updated: 2026-07-26T00:00:00Z
tags: [noema, budget, metering]
---

# Budget package

The `budget` package meters every noema LLM request against a shared token limit.

See the [noema package guide](../README.md) for the complete package map.

## Public surface

[`noema.budget`](./__init__.py) exports four types:

- [`TokenLedger`](./ledger.py) tracks shared and per-account token use.
- [`CallRecord`](./ledger.py) stores the usage and provenance for one logical call.
- [`BudgetExhausted`](./ledger.py) stops a request when no budget remains.
- [`BudgetedLLM`](./llm.py) sends chat-completion requests and charges the ledger.

## Composition

[`NoemaController`](../controller.py) creates one `TokenLedger` for each run.
It creates separate `BudgetedLLM` clients for mutation and coordination calls.
Each client checks the ledger before a request.
Each client charges reported usage after a billed attempt.
The controller stores the ledger state in checkpoints and run logs.

## Guarantee responsibility

This package owns the metering-integrity guarantee.
All mutation and coordination LLM calls use the same ledger.
The ledger records account, tag, model, token counts, attempt count, and iteration.

The [budget tests](../../tests/test_noema_budget_ledger.py) verify accounting and exhaustion behavior.
