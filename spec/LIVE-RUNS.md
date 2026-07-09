# LIVE-RUNS SPEC — the human-in-the-loop protocol for evolution runs

> Law (constitution + contract): **no agent ever launches a live run.** Agents
> prepare tickets and verify artifacts; the user launches. This file defines
> exactly what each side does. Cluster mechanics: vault
> [[Running Noema Arm Comparisons on the Lab Cluster]] and
> [[Distributed Inference Cluster]].

## Lifecycle

```
agent: PREP ticket ──▶ user: REVIEW+LAUNCH ──▶ (run) ──▶ agent: VERIFY ──▶ agent: FILE
         │                    │                              │  fail: run is
         └ preflight green    └ the only human-gated step    └  quarantined, never cited
```

## 1. Ticket (agent prepares; one file per run session)

Path: `loop/runs/queue/RT-NNNN-<slug>.md`. A ticket is a *session*: one or more
runs launched together on separate nodes. Required fields:

```markdown
---
ticket: RT-NNNN
status: queued        # queued | launched | verifying | verified | failed
runs:                 # one row per run in this session
  - {arm: null, benchmark: circle_packing, seed: 42, node: TBD, port: 8090}
  - {arm: pes,  benchmark: circle_packing, seed: 42, node: TBD, port: 8091}
budget_tokens: 1000000
config: <path>        # + sha256 of the frozen config file
expected: {tokens_per_run: ~1.0M, wallclock_per_run: ~2.6h}
---
## Purpose            — one sentence, which spec milestone this serves
## Preflight          — checklist below, each item with evidence pasted in
## Launch             — the exact commands, copy-pasteable, in order
## Watch              — the grep lines to check mid-run
## Teardown           — per-node kill list, "only what you started"
```

### Preflight (agent-side, no cluster access needed)

- [ ] `loop/guardrails/verify.sh` green on the commit to be run; commit hash in ticket
- [ ] Config: `coordination.module` is the ONLY diff between paired arm configs
      (`diff` output pasted); prompt-config constants match STUDY.md; seed set
- [ ] Budget in config == `budget_tokens` in ticket
- [ ] Benchmark evaluator subprocesses the candidate program (grep evidence —
      the Evaluator is not a sandbox)
- [ ] Run-dir naming: `examples/<benchmark>/runs/<ticket>-<arm>-s<seed>/`
      (never overwrites an existing dir)

### Preflight (user-side or supervised session, needs cluster)

- [ ] `cluster.py status`: chosen nodes idle; never repurpose a server you did
      not start (check `ps -o etimes=`)
- [ ] Model file present on node (`ls /var/tmp/models/`); server flags match
      STUDY.md pins (`-ngl 99 --ctx-size 10240 -np 1`, no `-ctk/-ctv`)
- [ ] Tunnels up: `curl -sf localhost:<port>/health` per node

## 2. Launch (user only)

The user runs the ticket's Launch block (nohup per run, separate logs). Ticket
`status: launched`, with start time and node names filled in. Mid-run checks are
the Watch block (context-overflow count, no-diff count, score movement) — at the
user's discretion; nothing automated acts on a live run.

## 3. During the run — agents may

- read logs/artifacts read-only; summarize on request
- prepare the next ticket
- **never**: restart servers, resubmit failed iterations, edit configs mid-run,
  or "fix" anything about a running experiment

## 4. Post-run verification (agent, deterministic, read-only on the run dir)

Every check is a command; a run is **verified** only when all pass. Failures
quarantine the run (`status: failed`, dir renamed `<dir>.quarantine-<date>`,
never cited, never deleted).

- [ ] **Ledger exact**: sum of `total_tokens` over `llm_calls.jsonl` ==
      `ledger.spent()` from the final checkpoint; zero `total_tokens: null` rows
- [ ] **Budget respected**: total ≤ 1,000,000 + one final-call overshoot max;
      run ended via BudgetExhausted (not crash/kill), final checkpoint present
- [ ] **Island distribution**: stored programs span all configured islands
      (the 0032 regression check), or all subtrees for tree runs
- [ ] **Zero context-overflow** errors in the run log
- [ ] **Prompt identity** (paired arms): shared prefix byte-identical on logged
      prompts; only the delimited coordination suffix differs
- [ ] **Config delta** (paired arms): `coordination.module` only
- [ ] **Seed/config match**: run dir's recorded config sha256 == ticket's

Automation: `make verify-run DIR=<run-dir> TICKET=<ticket>` (script to be added
at `loop/scripts/verify-run.sh`; each bullet = one function, exit non-zero on
first failure; the loop may implement this as a normal task).

## 5. Filing (agent)

- Append one row per run to `loop/runs/ledger.tsv`:
  `date  ticket  arm  benchmark  seed  tokens  best_score  pop_mean  verified`
- Move ticket to `loop/runs/done/`; write the vault run note (metrics, anomalies,
  links) per vault conventions; update INDEX
- Re-run `loop/verify-goals.sh` (ledger-completeness-live consumes fresh runs)

## Standing rules

- One ticket per session; a ticket is immutable once `launched` (amendments =
  new ticket referencing the old)
- Anything anomalous mid-run → finish or kill is the user's call; a killed run
  is quarantined, not verified
- Run dirs are raw study data: never modified, never deleted (constitution)
- Tickets, ledger, and this spec are committed to the repo; run dirs stay
  untracked (size) but their verification records live in the ticket
