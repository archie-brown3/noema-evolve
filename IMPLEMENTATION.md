# Triad audit — 2026-07-10

Wrote the read-only audit to
`loop/memory/audits/2026-07-10-triad-commit-audit.md` covering commits
887ac98, e161972, 3bfe346, 6f8797a, 56359f3 (files touched, triad areas,
same-commit test-extension verdict for each — all four code-bearing commits
verdict YES, 56359f3 is N/A/no triad code touched), plus the base.py history
slice (changed once, in 6f8797a, adding the sanctioned `retry_advice` hook),
uncommitted-diff check (empty at audit time), and the unittest tail
(`Ran 125 tests in 0.236s` / `OK`).

Independently re-verified 2026-07-10: every commit stat and the base.py
history claim cross-checked directly against `git show`/`git log` — accurate.

# Deviation — 0063 C1 split (2026-07-11)

The planned C1 (faithful planner constants + their prompt-test pins) measured
249 changed lines — over the 200-line/commit law. Conservative option taken:
split into C1a (`noema/coordination/pes/planner.py` constants only, 170 lines)
and C1b (`tests/test_noema_prompts.py` pins, 79 lines), committed back-to-back.
Triad reading: C1a adds dead constants only — no emitted prompt changes, no
prompt-identity effect, existing prompt tests unmodified and green — so the
same-commit test-extension obligation binds at C2 (prompt_variant wiring),
which will extend test_noema_prompts.py again in that commit.

# Deviations — 0063 verifier follow-ups (2026-07-11)

Fresh-context verifier PASS; SHOULD-FIX findings applied: faithful plan call
now sends the 2048 floor explicitly even with no configured cap (finding 1),
and the custom regression pin freezes the template bytes with sha256 literals
(finding 2). An empty plan slice after the heading now logs a gate-1 warning
(finding 5). Recorded KEEP-section deviations (finding 3/4): upstream's un-raw
`\times` renders tab+`imes` at runtime — we render the intended `\times`,
annotated `# NOEMA` in planner.py; `{island_num}` derives from the provider's
length (single fetch) with a `ctx.island + 1` fallback only in provider-less
test/degenerate runs (live runs always inject the provider).

# Deviations — 0064 (2026-07-11)

Verifier seat substitution: the DISPATCH table routes the verifier to
claude-fable-5, which returned "out of usage credits" mid-run for this task.
Conservative option taken: ran the identical fresh-context check (same bundle,
single Read, no other tools) on Opus 4.8 rather than skipping the independent
check or self-assessing. Verdict PASS. If fable credits return before 0065,
the seat goes back to fable.

Verifier SHOULD-FIX findings applied: (1) an absent/None parent_id (GENESIS or
a pre-0064 checkpointed queue entry) rendered "0 out of 0" sibling stats the
prompt tells the model to copy — it now degenerates to the only-child case;
(2) pipes in a plan digest are escaped so they cannot add phantom columns to
the stats table (the finding's newline half was checked and is already handled
— _extract_strategy flattens); (3) the cap test was a tautology (fixture under
cap) — an over-cap fixture now exercises the truncation branch; (4) the
preamble strip was unpinned (the happy path never needs it) — a malformed-
header fallback fixture now pins it; (5) all four faithful constants are
sha256-pinned, so the KEEP lines (the fidelity claim itself) are machine-
checked, not just substring-asserted.

Accepted as-is, recorded not fixed: the pre-flight size guard's 4-chars/token
estimate is optimistic for JSON-escaped code (~3 chars/token), so it is a
guard against gross overflow rather than an exact bound, and
context_window_tokens (10240) is a hand-maintained mirror of the substrate's
window. Both are knobs the shakedown (RT-0003) measures against real prompts
before the matrix.
