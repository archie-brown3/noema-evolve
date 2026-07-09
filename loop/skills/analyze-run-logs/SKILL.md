---
name: analyze-run-logs
description: Read-only analysis of experiment run artifacts (llm_calls.jsonl, checkpoints) — spend by account, null-usage rows, iteration counts, anomalies.
when: a run finished, or the ledger-completeness-live goal fails, or the user asks what a run did.
---

## Steps
1. Identify the newest run dirs: `ls -dt examples/*/noema_*output*`.
2. jq over `llm_calls.jsonl`: totals by account (mutation vs coordination), rows with
   `total_tokens == null`, attempts distribution, per-iteration spend.
3. Write findings as a short report (STATE.md entry + vault note if substantial).

## Never
- Never modify, move, or delete anything inside a run dir — they are the study's raw data.
- Never draw cross-arm conclusions from runs at unequal token spend.

## Done when
- Report states: total tokens by account, null-usage row count, iterations completed,
  and one-line verdict per anomaly — each claim citing the jq output that supports it.
