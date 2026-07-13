---
title: Circle-packing enriched sweep — basic log review
date: 2026-07-13
tags: [noema, experiment, evidence, circle-packing, token-budget]
---

# Circle-packing enriched sweep — basic log review

Initial read-only review of the seed-42 enriched-prompt sweep from
[[tasks/0070-all-arms-enriched-sweep]]. The run used Qwen3-30B-A3B-Instruct-2507-Q8_0,
the islands + MAP-Elites substrate, and a nominal 1M-token budget. The runbook is
[[Running Noema Arm Comparisons on the Lab Cluster]].

## Result summary

| Arm | Structured calls | Prompt tokens | Completion tokens | Total tokens | Evaluations | Non-zero evaluations | Best combined score | Outcome |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| null | 85 mutation | 963,900 | 38,161 | 1,002,061 | 86 incl. seed | 21/86 | **0.555497** | Budget exhausted |
| pes-custom | 113 coordination + 58 mutation | 937,610 | 75,867 | 1,013,477 | 58 | 42/58 | **0.741335** | Budget exhausted |
| pes-faithful | 5 coordination + 12 mutation | 72,459 | 26,400 | 98,859 | 13 | 1/13 | **0.364200** | Aborted by context-fit assertion |

![[circle-packing-token-fitness-2026-07-13.png]]

## Basic interpretation

- `pes-custom` reached 0.741335, versus 0.555497 for `null`: a raw difference of
  +0.185838 (+33.4% relative to null's score).
- The PES gain appeared early: 0.741335 was first reached at mutation iteration
  13. Null's final improvement to 0.555497 appeared at iteration 68 and then
  plateaued.
- The two completed arms are approximately equal on total structured token
  spend: 1,002,061 versus 1,013,477. This is the relevant comparison for the
  study's equal-token basis, subject to the accounting caveat below.
- Island coverage was present in both completed runs: null had program metadata
  on islands 0–3 (23/21/21/21 programs), and pes-custom on islands 0–3
  (16/14/14/14 programs). This basic check does not replace the full run
  verifier.

## Observed run failures and caveats

- Null produced 65 evaluator failures, all dominated by generated programs
  raising `IndexError: index 26 is out of bounds for axis 0 with size 26`.
  These are invalid candidate programs, not yet a demonstrated evaluator bug.
- PES-custom produced two evaluator failures with the same `IndexError`, plus
  one “no valid program in LLM response” event.
- PES-faithful is not a comparable result. It generated only one non-zero
  evaluation, then stopped when its reflection prompt was estimated at ~6,268
  prompt tokens plus 4,096 reserved completion tokens, exceeding the configured
  10,240-token context window. Its run log also contains generated-code failures
  for missing `linprog` and `differential_evolution` names.
- The PES-custom stop line says `account 'coordination'` spent 1,013,477 tokens,
  but `llm_calls.jsonl` attributes 243,934 tokens to coordination and 769,543 to
  mutation. The total reconciles to the JSONL, but the account label in the stop
  message is misleading and should be checked during the later metering/code
  review.
- The log contains no `exceed_context_size_error` strings for null or PES-custom;
  the faithful failure is an explicit preflight assertion instead.

## Files reviewed

- `examples/circle_packing/runs/null-30b-enriched-s42.log`
- `examples/circle_packing/runs/null-30b-enriched-s42/llm_calls.jsonl`
- `examples/circle_packing/runs/pes-custom-30b-enriched-s42.log`
- `examples/circle_packing/runs/pes-custom-30b-enriched-s42/llm_calls.jsonl`
- `examples/circle_packing/runs/pes-faithful-30b-enriched-s42.log`
- `examples/circle_packing/runs/pes-faithful-30b-enriched-s42/llm_calls.jsonl`

This is a descriptive first pass only. The next pass should review the vault's
unclosed tickets and the relevant accounting, evaluator, prompt-fit, and runner
code before treating the arm difference as a mechanism result.

Related: [[Noema Architecture]] · [[Distributed Inference Cluster]] ·
[[tasks/0025-fix-ledger-metering-local-inference]] ·
[[tasks/0042-fix-pes-lineage-loss-on-plan-failure]] ·
[[tasks/0067-pes-faithful-context-fit-remediation]]
