predicate: f=$(ls -t /root/noema-evolve/examples/*/noema_*output*/llm_calls.jsonl 2>/dev/null | head -1) && [ -n "$f" ] && [ "$(jq -s '[.[] | select(.total_tokens == null)] | length' "$f")" -eq 0 ]
born: 2026-07-08
source: loop-system setup — found VIOLATED at enrollment: the 2026-07-08 live runs against local Qwen nodes logged total_tokens null on every row, so the token ledger is blind exactly where the study runs. Deliberately enrolled failing; vault task 0025 is the fix.
status: VIOLATED
last-pass: never
on-violation: queue the fix task (0025-fix-ledger-metering-local-inference); do not auto-fix; no cross-arm conclusions from affected runs.
retire-when: superseded by a stricter reconciliation goal after the first provider-dashboard reconciliation (vault task 0028). Human decision, logged.
