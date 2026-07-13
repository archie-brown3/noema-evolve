# LOOP-AUTONOMY SPEC (DRAFT) — closing the gap between "autonomous" and "pages me for live runs"

> Status: **draft, unreviewed**. Written in response to the request: "review the
> loop — I want it setup by next week, with a clear spec [so] it will continue
> the work... to work autonomously and ping me (telegram) when we need to run
> experiments live on the inference cluster."
>
> This document proposes changes. It does not make them. Nothing under `loop/`
> has been touched. §4 is a punch-list for a human (or a dispatched worker,
> once this spec itself is reviewed and turned into a vault task) to apply as
> real diffs.
>
> Read first: `loop/RUNBOOK.md` (alarms + trust schedule), `loop/contract.md`
> (blast radius), `loop/loop.sh` (the heartbeat), `spec/LIVE-RUNS.md` (ticket
> lifecycle), `spec/STUDY.md` (milestones this loop serves).

---

## 1. The new wake condition

### 1.1 What happens today (confirmed by reading `loop.sh`)

The conductor emits one of three actions: `execute`, `queue`, `stop`. The
`queue` branch is exactly this line (`loop/loop.sh:144`):

```bash
[ "$ACTION" = queue ] && { echo "- queued: $SKILL — $(jq -r .item work-order.json)" >> memory/STATE.md; continue; }
```

That is the entire handling: one line appended to `memory/STATE.md`, then the
loop moves to the next iteration. No telegram call. Nobody is paged. A
human only discovers a queued item by running `make queue` (RUNBOOK's daily
commands) or by reading `STATE.md` directly. This is correct behavior for
*ordinary* contract-sensitive work (e.g. "this touches `noema/budget/`,
a human should read the diff before it happens") — those items can wait for
the next `make tick`-with-coffee session. It is the wrong behavior for one
specific case: **a live-run ticket has finished prep and is sitting there
fully green, and a study milestone is now blocked purely on the human
pressing go.** That case should page immediately, the same tick it's
discovered, because the cost of the delay is calendar time against the
Sep 2026 deadline, not just review latency.

The reusable pattern is `wake_user()` (`loop/loop.sh:25-29`):

```bash
wake_user() {
  echo "WAKE: $1" >&2
  [ -x /home/archie/scripts/send-telegram.sh ] \
    && /home/archie/scripts/send-telegram.sh "noema loop: $1" >/dev/null 2>&1 || true
}
```

It's already called for three cases: safeguard-router reroute (exit 2),
verify-FAILED-twice, and daily-usage-cap-mid-tick. `contract.md`'s "wakes me
up" list is the authoritative enumeration of when this fires; a live-run
ticket going green is not currently on that list.

### 1.2 The distinguishing test: "ordinary queue" vs. "ticket ready to launch"

These are categorically different questions being routed to the same
`action: queue` bucket today:

| | ordinary queue | ticket-ready |
|---|---|---|
| What's being asked | "should the loop be allowed to make this change" | "the loop has finished everything it can do; only you can press go" |
| What the human does | reads a diff/spec, decides, maybe next coffee session | reviews the ticket's preflight evidence, runs the Launch block on the cluster |
| Urgency | can wait for `make queue` | blocks a study milestone every day it sits |
| Where the artifact lives | `work-order.json` / STATE.md line | `loop/runs/queue/RT-NNNN-*.md` (per LIVE-RUNS §1) |

The conductor needs one additional fact per tick to tell these apart: **does
`loop/runs/queue/` contain a ticket file whose frontmatter `status: queued`
and whose Preflight checklist has zero unchecked boxes?** That's mechanical —
grep, not judgment — so it should be computed once, cheaply, and handed to
the conductor as a fact rather than left for the (expensive, fable-tier)
conductor to re-derive from a raw file read.

Concretely: `status: queued` in a ticket's frontmatter, per LIVE-RUNS.md §1,
is only ever set once every "Preflight (agent-side)" box is checked (the
ticket schema doesn't define an intermediate "half-prepped" status — a
ticket is either still being drafted, in which case it isn't committed with
`status: queued` yet, or it's queued, meaning agent-side preflight is done).
So the check reduces further to: **does a file matching
`loop/runs/queue/RT-*.md` exist with `status: queued`, and does the study
milestone table (`spec/STUDY.md`) currently need it launched** (i.e. is
today's date inside or past the relevant milestone window, or is this the
next unlaunched ticket in sequence)? The second half needs judgment (which
milestone, is this actually the blocking ticket) — that's exactly what the
fable-tier conductor is for; the first half is a pure grep the triage seat
(haiku, cheap) should do.

### 1.3 Proposed mechanism (see §4 for the literal diffs)

1. **Triage** (`triage.md`, cheap/haiku) gets one new rule and `loop.sh`'s
   signal assembly gets one new stanza so triage can see ticket files at all
   (today the `SIG` heredoc never looks at `loop/runs/queue/`). Triage emits
   a finding tagged `ticket-ready` — distinct from the existing
   `contract-sensitive` tag — when it sees a queued, fully-checked ticket.
2. **Conductor** (`conductor.md`, fable) gets one new routing rule: if the
   actionable item is tagged `ticket-ready`, the action MUST be a new value,
   `action: ticket-ready` — not `queue`, not `execute`. The conductor still
   decides *which* ticket is the milestone-blocking one if more than one
   qualifies (judgment), but it may not silently fold this into ordinary
   `queue`.
3. **loop.sh** gets one new branch, sibling to the existing `queue` branch,
   that calls `wake_user` with the ticket id and reason, then logs to
   `STATE.md` and continues the loop (same non-blocking shape as the
   existing queue branch — the loop keeps working on other items after
   paging).
4. **contract.md** gets one new bullet under "wakes me up" naming this case
   explicitly, so the blast-radius document stays the single source of
   truth for what pages the human (today it lists 6 conditions; a live-run
   ticket going green is a 7th, and arguably the most important one given
   the user's stated goal).

This deliberately does *not* let the loop decide a ticket needs a different
kind of review, edit it, or resubmit it — LIVE-RUNS.md §3 already forbids
agents from touching tickets mid-flight, and this wake condition fires
*before* launch, not during. The loop's job stays exactly "notice it's ready,
say so" — the human still does 100% of the Launch step, per the constitution.

---

## 2. Is "autonomous by next week" compatible with the 30-day trust schedule?

### 2.1 The tension, stated plainly

RUNBOOK.md's schedule is explicit and the table's own logic is a safety
mechanism, not a formality:

| Week | Level | Human does | Graduates when |
|---|---|---|---|
| 1 | L1 report | `make tick` by hand daily, reads everything | 3 consecutive runs route exactly as the human would have |
| 2 | L2 draft | cron on; `make queue` with coffee | 2 skills cross 20 logged runs |
| 3 | L3 ship | best skill goes unattended | 1 week, zero interventions |
| 4 | L4 grow | compost sign-offs, delete pass | something removed, nothing broke |

Cron is explicitly gated: *"Cron (install at Week 2, not before)"*. Unattended
shipping (PRs merging without a human reading them first) is gated to Week 3,
and only for "the best skill," only after a full week with zero interventions.

**Where the study actually is right now:** `memory/dispatch.tsv` and
`memory/STATE.md` show exactly one real tick (2026-07-09T04:00), and that
tick did not route cleanly — the worker's diff on `fix-ledger-metering`
failed verify (out-of-scope files, a wrong fallback-estimation branch, a
missing test fixture), and the fix that actually landed (`e161972`) was
authored and committed by the human by hand, outside the loop, after reading
the worker's draft. That is, definitionally, an "intervention." So the
Week-1 graduation counter (3 *consecutive* clean routes) currently stands at
**zero**, not one — a single non-clean tick resets it under the schedule's
own rule.

If "autonomous by next week" is read as *"the loop ships PRs unattended and
launches into the next round of work without me watching"* — that is Week-3
behavior. Reaching it by next week would require skipping the Week-1
graduation gate (3 clean consecutive routes, currently 0) and the Week-2
gate (2 skills × 20 logged runs, currently 0 skills have any logged runs)
entirely. The schedule's whole point is that trust is earned from evidence,
not granted on a calendar; skipping straight to unattended shipping on a
system that has produced exactly one tick, which needed a hand-fix, is the
precise failure mode the schedule exists to prevent — and separately, the
constitution's live-run law ("never start a live LLM run... unattended")
means unattended shipping was *never* going to include launching cluster
runs regardless of trust tier; that part is permanently human-gated, not a
function of weeks elapsed.

If instead "autonomous" is read as *"I stop needing to manually type
`./loop.sh` every day; it runs on a schedule, keeps prepping tickets and
drafting work, and pages me exactly when something needs my judgment (a
review, a launch decision) rather than silently waiting in a file I have to
remember to check"* — that is achievable by next week, and it does not
require skipping any graduation. Concretely: cron installing (Week 2's
mechanical change) doesn't by itself imply unattended *shipping* — shipping
is gated separately by the trust tier in `scripts/trust-log.sh --tier`
(`loop.sh:186`), which already routes non-`auto`-tier skills to a draft
commit + `review:` STATE.md line instead of a PR (`loop.sh:192-197`). A
skill with zero logged runs cannot be `auto` tier, so cron running daily
against a fresh system would, mechanically, keep producing draft branches
and queue/ticket-ready pages for a while regardless — the trust ledger
already enforces the "don't ship until earned" property independent of
calendar weeks. What cron changes is *whether the human has to remember to
invoke the loop*, not whether the loop is allowed to ship.

### 2.2 Recommendation (flagged as the human's call, not mine)

Read "autonomous" as the second interpretation: **cron runs the tick on
schedule; the loop keeps prepping tickets, running the goal ledger, and
drafting work without a human manually invoking anything; it queues
ordinary contract-sensitive decisions and pages telegram for ticket-ready
and the existing wake conditions; it does not ship anything unattended
until the trust ledger says a skill is `auto` tier**, which happens through
the existing mechanism (`trust-log.sh`) regardless of what week the calendar
says. Concretely for next week:

- Run 2-3 more manual `make tick` sessions this week (Week-1 behavior,
  human reads everything) specifically to rebuild the "3 consecutive clean
  routes" counter that the 2026-07-09 tick reset. If those come back clean,
  Week-1's own graduation criterion is met on its own evidence, not skipped.
- Install cron (§4 below) once that counter is at 3 — which, given the pace
  of one tick roughly per session, is plausibly *this coming week* rather
  than a violation of "Week 2, not before": the schedule counts graduations,
  and this system may simply graduate fast if the next few ticks are clean.
  If they aren't clean, cron waits — the date on the calendar doesn't
  override the evidence requirement.
- Ship-unattended (Week 3) is not claimed as a "by next week" goal at all;
  it stays contingent on a full week of zero interventions on some specific
  skill, exactly as written.

This satisfies "continues the work... autonomously and pings me for live
runs" without asking the schedule to certify trust it hasn't yet observed.
But this is a judgment call about what the user meant by "autonomous," and
the user should say explicitly which reading they intended — the two
readings produce very different Week-1-of-next-week behavior (hand-run tick
vs. cron-run tick), and only the human can pick.

---

## 3. Milestone-to-goal mapping (W1, W2 exit criteria)

`spec/STUDY.md`'s milestone table, W1 and W2 rows, each decomposed into its
listed sub-criteria:

### W1 (–Jul 19): "metering fix merged; smoke run ticket RT-0001 verified
(ledger goal passes); shakedown ticket RT-0002 (3-seed null-vs-pes) launched"

| Sub-criterion | Existing goal? | Status |
|---|---|---|
| metering fix merged | No existing goal checks "merged into the tracked branch" — `loop/goals/metering-integrity.md` checks the *code* passes the triad tests in whatever's checked out, and `ledger-completeness-live.md` checks the *newest run log*, but neither checks that `e161972` (currently only on branch `loop/fix-ledger-metering-1783569667`, task 0025 still `status: in-progress`, not in `tasks/done/`) has actually landed on the main tracked branch. | **Gap — new goal needed** |
| smoke run RT-0001 verified, ledger goal passes | `loop/goals/ledger-completeness-live.md` covers the "ledger goal passes" half exactly (its predicate literally is the check STUDY.md refers to as "the ledger goal"). It does **not** cover "RT-0001 specifically" (it greps the newest run dir by mtime, not by ticket id) or "verified" in the full LIVE-RUNS §4 sense (7 checks; only implemented once task 0038's `verify-run.sh` lands). | **Partial — existing goal covers one clause; new goal needed for the ticket-specific, fully-verified clause** |
| RT-0002 launched | No existing goal — `loop/runs/` doesn't exist yet (no tickets have been drafted). | **Gap — new goal needed** |

### W2 (–Jul 26): "s1 arm merged (tests incl. hand-computed traces); bin
packing ported (role-structured, eval subprocessed); config freeze commit
tagged"

| Sub-criterion | Existing goal? | Status |
|---|---|---|
| s1 arm merged, hand-computed-trace tests | No existing goal — `noema/coordination/` currently has only `base.py`, `hifo/`, `pes/` (confirmed by listing); task 0035 is `status: todo`. | **Gap — new goal needed** |
| bin packing ported, role-structured, eval subprocessed | No existing goal — `examples/` has no `bin_packing` directory yet; task 0036 is `status: todo`. | **Gap — new goal needed** |
| config freeze commit tagged | No existing goal — no git tags exist in the repo at all (confirmed: `git tag` is empty). | **Gap — new goal needed** |

Every W1/W2 sub-criterion that isn't already a standing goal is genuinely
unmet right now (all three underlying tasks — 0025, 0035, 0036 — are
`todo`/`in-progress`, not `done`), which is expected this early, but it
means the goals below would enroll **VIOLATED**, same pattern as
`ledger-completeness-live.md` did at its own enrollment (deliberate — see
that file's `source:` line). That's correct: a goal that starts green
because you defined success as "whatever's currently true" isn't verifying
anything.

### Proposed new goals

Two goals, one per milestone, each a conjunction of that milestone's gaps
(matches the existing convention of one goal file per coherent standing
condition — see `ledger-completeness-live.md`, whose predicate is already a
multi-clause one-liner in this style). File format matched to
`loop/goals/tests-green.md`.

```
# loop/goals/w1-metering-and-tickets.md
predicate: cd /root/noema-evolve && git merge-base --is-ancestor e161972 origin/claude/noema-integration-plan-3u3cgy 2>/dev/null && [ -f /root/claude-brain/tasks/done/0025-fix-ledger-metering-local-inference.md ] && f=$(ls loop/runs/done/RT-0001-*.md loop/runs/queue/RT-0001-*.md 2>/dev/null | head -1) && [ -n "$f" ] && grep -q '^status: verified' "$f" && grep -q '^RT-0001' loop/runs/ledger.tsv 2>/dev/null && g=$(ls loop/runs/queue/RT-0002-*.md loop/runs/done/RT-0002-*.md 2>/dev/null | head -1) && [ -n "$g" ] && grep -qE '^status: (launched|verifying|verified)' "$g"
born: 2026-07-09
source: spec/LOOP-AUTONOMY.md §3 — W1 exit criterion from spec/STUDY.md's
  milestone table, decomposed: metering fix merged to the tracked branch
  (task 0025 in tasks/done/), RT-0001 verified with a ledger row, RT-0002
  launched or further. Deliberately enrolled VIOLATED — none of 0025/RT-0001/
  RT-0002 exist yet on the tracked branch as of enrollment.
status: VIOLATED
last-pass: never
on-violation: this IS the W1 tracking goal, not an incident to fix — expected
  VIOLATED until W1's real work lands. Do not treat a VIOLATED reading before
  2026-07-19 as an alarm; treat it as an alarm only if still VIOLATED after
  2026-07-19 (the milestone deadline).
retire-when: superseded by the W2 goal once W1 passes, or by the study
  ending. Human decision, logged.
```

```
# loop/goals/w2-s1-binpacking-freeze.md
predicate: cd /root/noema-evolve && [ -f /root/claude-brain/tasks/done/0035-implement-s1-lineage-arm.md ] && python3 -c "from noema.coordination import MODULE_REGISTRY; assert 's1' in MODULE_REGISTRY" && [ -f /root/claude-brain/tasks/done/0036-port-bin-packing-benchmark.md ] && [ -d examples/bin_packing ] && python3 -m unittest discover tests >/dev/null 2>&1 && git tag | grep -q '^config-freeze'
born: 2026-07-09
source: spec/LOOP-AUTONOMY.md §3 — W2 exit criterion from spec/STUDY.md's
  milestone table, decomposed: s1 arm registered + task 0035 done, bin
  packing benchmark dir present + task 0036 done, full suite green, a
  config-freeze git tag exists. Deliberately enrolled VIOLATED — none of
  these exist yet as of enrollment (confirmed: no noema/coordination/lineage/,
  no examples/bin_packing/, no git tags).
status: VIOLATED
last-pass: never
on-violation: this IS the W2 tracking goal, not an incident to fix — expected
  VIOLATED until W2's real work lands. Treat as an alarm only if still
  VIOLATED after 2026-07-26 (the milestone deadline).
retire-when: superseded by the W3-W4 goal once W2 passes, or by the study
  ending. Human decision, logged.
```

Two notes on these drafts a human should sanity-check before enrolling them
for real:
- The `git merge-base --is-ancestor e161972 origin/...` clause hardcodes a
  commit hash. That's fine as a one-time landing check, but if the fix gets
  rebased/squashed on merge the literal hash disappears from history even
  though the fix landed — a human applying this goal for real should verify
  against whatever the actual merge produces, or swap the clause for
  something content-based (e.g. the new test name existing and passing on
  the tracked branch).
- `on-violation` for both of these deliberately deviates from the "wake me"
  pattern used by `metering-integrity.md`/`prompt-identity.md` — waking the
  human every tick for a milestone goal that's *expected* to be VIOLATED
  until its deadline would be noise, not signal. `verify-goals.sh` as
  written today pages telegram on **any** VIOLATED goal with no per-goal
  suppression — see punch-list item 4d below; these two goals should not be
  enrolled until that's addressed, or they'll page daily starting now.

---

## 4. Punch-list — concrete line-level changes (proposed, not applied)

Numbered for reference in the vault tasks below. Each item names the file,
the change, and why. These are prose/pseudocode diffs for a human to review
and apply deliberately — no patch has been generated or applied.

1. **`loop/loop.sh` — signal assembly (the `SIG` heredoc, currently lines
   94-104).** Add a stanza after the "newest run dirs" line:
   ```
   echo "== live-run ticket queue =="
   for f in loop/runs/queue/RT-*.md; do
     [ -e "$f" ] || continue
     echo "-- $f --"; grep -E '^(ticket|status):' "$f"
     echo "unchecked preflight: $(grep -c '^\s*- \[ \]' "$f")"
   done
   ```
   Why: triage currently has no visibility into `loop/runs/queue/` at all —
   the `SIG` block only looks at git log, gh issues/runs, the vault INDEX,
   the goal ledger tail, and run dirs under `examples/`. Without this, no
   seat can ever see a ticket exists, let alone that it's ready.

2. **`loop/triage.md` — new rule.** Add to the "Rules" list (after the
   existing "touching noema/coordination/base.py... = always actionable,
   noted contract-sensitive" line):
   ```
   - A ticket block under "live-run ticket queue" with status: queued and
     "unchecked preflight: 0" is always actionable, noted "ticket-ready"
     (not "contract-sensitive" — do not conflate the two tags).
   ```
   Why: makes the cheap seat responsible for the mechanical detection (grep,
   not judgment), matching the existing division of labor where triage is
   "a reader, not a decider" but does flag categories.

3. **`loop/conductor.md` — new routing rule and output schema change.**
   In the numbered routing rules (currently under "2."), add:
   ```
   - a "ticket-ready" finding from triage -> action: ticket-ready (never
     fold this into "queue" — it pages the human immediately; ordinary
     queue does not). If more than one ticket qualifies, pick the one that
     unblocks the nearest spec/STUDY.md milestone deadline; name it in
     "item".
   ```
   And extend the output contract line (currently `"action":
   "execute|queue|stop"`) to `"action": "execute|queue|stop|ticket-ready"`.
   Why: today's conductor has no way to express this decision even if it
   noticed the ticket itself — the schema caps action at three values.

4. **`loop/loop.sh` — new branch, sibling to the existing queue branch
   (currently line 144).** Immediately after that line, add:
   ```bash
   [ "$ACTION" = ticket-ready ] && {
     wake_user "live-run ticket ready to launch: $(jq -r .item work-order.json)"
     echo "- ticket-ready: $(jq -r .item work-order.json) — paged" >> memory/STATE.md
     continue
   }
   ```
   Why: this is the actual page. Mirrors the existing queue branch's shape
   (log + `continue`, no worktree/worker/verify machinery — there's nothing
   to execute, the ticket already did the prep).

   4a. Also add per-goal wake suppression to `loop/verify-goals.sh`, if the
   two milestone goals in §3 are enrolled: the script currently pages on
   *any* `VIOLATED` goal with no way to say "expected violated until date
   X." Cheapest fix: read an optional `on-violation:` prefix convention
   (e.g. a `suppress-until: 2026-07-19` frontmatter key) and skip that
   goal's name from the telegram message (not from the ledger row) while
   `date` is before `suppress-until`. This is a judgment call for whoever
   applies the diff — flagging it, not prescribing the exact mechanism.

5. **`loop/contract.md` — new "wakes me up" bullet.** Add to that list
   (currently 6 items):
   ```
   - a live-run ticket (loop/runs/queue/RT-*.md) has status: queued with
     every preflight box checked — ready to launch
   ```
   Why: contract.md is the single declared source of truth for blast radius
   and paging conditions; the mechanism in items 1-4 must be reflected here
   or the document silently drifts from what the code does — exactly the
   failure this task is trying to prevent.

6. **`loop/RUNBOOK.md` — new alarm row** in the "Alarms" table:
   ```
   | ticket-ready (telegram) | a live-run ticket cleared preflight and is
   | waiting on you | review the ticket's Preflight evidence, then run its
   | Launch block per spec/LIVE-RUNS.md §2 |
   ```
   Why: RUNBOOK.md is "every alarm the system can raise, and what to do" —
   this is a new alarm; leaving it out of the runbook means whoever's on
   call has to remember offline what a "ticket-ready" telegram message means.

7. **`loop/RUNBOOK.md` — trust-schedule note** (only if §2.2's recommended
   reading is adopted): add a one-line clarification under the trust
   schedule table that cron installing does not imply unattended shipping —
   shipping is separately gated by `trust-log.sh --tier`. This is
   documentation only (no behavior change; `loop.sh` already enforces this
   at line 186), added because the ambiguity in §2 of this spec came from
   the RUNBOOK conflating "cron on" with "ships unattended" nowhere in
   writing, but the ambiguity was easy to read that way.

None of items 1-7 touch `noema/coordination/base.py`, experiment data, or
add a dependency. Item 1's diff is a handful of lines; items 2, 3, 5, 6 are
single-list-item insertions; item 4 is ~6 lines; item 4a and 7 are the two
softest/most judgment-dependent pieces and should probably be split into
their own follow-up rather than bundled with 1-6 (see task 0047 below,
which separates them).
