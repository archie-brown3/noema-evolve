# RUNBOOK — every alarm the system can raise, and what to do

The loop lives in `loop/`. Daily driving: `make tick` (heartbeat), `make queue`
(what waits for you), `make trust` (worker ledger), `make audit` (usage),
`make goals` (standing goals). Wake channel: telegram via
`/home/archie/scripts/send-telegram.sh`.

## Alarms

| Signal | Meaning | Action |
|---|---|---|
| exit 2 (reroute) | safeguard router served a model the loop didn't choose | read STATE.md; re-run the item tomorrow; never iterate on the swapped output |
| `REFUSAL(<seat>)` in STATE.md | safety classifier declined (HTTP 200, `stop_reason: refusal`) | conductor/verifier already carry `--fallback-model claude-opus-4-8`; if it recurs on one skill, audit that skill for reasoning-echo or cyber/bio-adjacent phrasing |
| exit 3 (usage cap) | today's notional usage hit `DAILY_USAGE_CAP` (default 15 units; nothing billed — subscription) | `make audit`; find which stage grew; fix the effort map first, raise the cap only if the work volume is real |
| exit 4 (session limit) | provider session/usage limit hit mid-tick | nothing — it resets on its own; STATE.md records the reset time; rerun after |
| `ALERT: <skill> demoted` | an established skill dropped below 90% | read its last 3 fails in STATE.md; usually the spec pattern, not the worker |
| goal VIOLATED (telegram) | something finished stopped being true | `make goals` for the ledger row; suspects = `git log --since=<last-pass>`; the fix goes through the pipeline, never patched inline |
| verify FAILED twice on one skill (telegram) | maker/checker standoff | you decide, or run a third fresh reviewer that judges evidence and may not split the difference |
| verify-goals predicate timeout (60s) | predicate too expensive | that is a violation; cheapen the predicate |
| iteration cap (exit 1) | 10 iterations without a stop decision | read STATE.md tail; usually a queue loop — clear the queue |

## Loop-specific notes

- The verifier sees only the repo worktree diff. Specs that require vault edits
  should keep those edits in the repo record (IMPLEMENTATION.md / STATE.md) or
  route vault writes through the `sync-vault-from-repo` skill, whose done-when is
  checkable from INDEX.md itself.
- Usage figures are the CLI's notional `total_cost_usd`. On a subscription plan
  nothing is billed; treat them as a relative meter per stage (the real ceiling
  is the provider session limit). `make audit` aggregates them exactly.
- A pre-tick cap breach exits 3 silently (it's routine throttling, already
  known); only a mid-tick breach pages telegram.
- Live experiment runs are NEVER started by the loop (constitution + contract).
  The loop preps configs and queues; you say go.

## Cron (install at Week 2, not before)

```
0 7 * * 1-5  cd /root/noema-evolve/loop && ./loop.sh >> memory/cron.log 2>&1
30 7 * * *   cd /root/noema-evolve/loop && ./verify-goals.sh >> memory/cron.log 2>&1
0 8 * * 0    cd /root/noema-evolve/loop && claude -p "$(cat compost.md)" --model claude-fable-5 --effort high --allowedTools "Read" >> memory/cron.log 2>&1
```

Add alongside the existing openclaw entries in root's crontab. The third line is
the weekly compost (see below).

## The 30-day trust schedule (do not skip graduations)

| Week | Level | You do | Graduate when |
|---|---|---|---|
| 1 | L1 report | `make tick` by hand daily; read everything | 3 consecutive runs route exactly as you would have |
| 2 | L2 draft | cron on; `make queue` with coffee; reviews feed the ledger | 2 skills cross 20 logged runs |
| 3 | L3 ship | `make audit` vs expectations; best skill goes unattended | 1 week, zero interventions |
| 4 | L4 grow | compost sign-offs; approve 1 proposed skill; run the delete pass | you removed something and nothing broke |

## Optional loops — install ONLY when the condition appears

- **Compost** (weekly, from Week 2 — the one exception, scheduled above): reads the
  week's exhaust (FAILED in STATE.md, fails in trust.tsv, FAILs in goal-ledger.tsv,
  PRs closed unmerged) and proposes AT MOST 3 changes — a new CLAUDE.md law
  (quoting incidents), a skill fix, or a missing standing goal. Propose only;
  human signature required. Prompt: `loop/compost.md`.
- **Quorum** (install when dispatch.tsv shows fable wake-ups that produced
  `action: stop`): three cheap-model votes before waking the conductor; wake on 2/3.
- **Ratchet** (install when one number matters — e.g. a benchmark score floor):
  monotonic improvement or self-revert; the metric may not be gamed; the finished
  floor becomes a standing goal.
- **Sparring** (install when shipping code daily): breaker writes one failing test
  against yesterday's diffs; builder fixes the code, never the test; disputes queue.
