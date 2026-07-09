---
ticket: RT-0002
status: queued        # queued | launched | verifying | verified | failed
runs:                 # one row per run in this session — 3 seeds x {null, pes}
  - {arm: null, benchmark: circle_packing, seed: 42, node: TBD, port: 8090}
  - {arm: pes,  benchmark: circle_packing, seed: 42, node: TBD, port: 8091}
  - {arm: null, benchmark: circle_packing, seed: 43, node: TBD, port: 8092}
  - {arm: pes,  benchmark: circle_packing, seed: 43, node: TBD, port: 8093}
  - {arm: null, benchmark: circle_packing, seed: 44, node: TBD, port: 8094}
  - {arm: pes,  benchmark: circle_packing, seed: 44, node: TBD, port: 8095}
budget_tokens: 1000000
config: examples/circle_packing/run_noema_arm.py (+ CLI flags below)  # NO frozen
  # config.yaml/.json exists for any noema run to date — see Preflight note below
  # and [[tasks/0041-persist-frozen-run-config-with-hash]]. sha256: N/A (blocked
  # on 0041; substitute is the pasted flag list + a git commit hash instead).
expected: {tokens_per_run: ~1.0M, wallclock_per_run: ~2.6h}
---

## Purpose

W1 milestone (`spec/STUDY.md`): 3-seed `null`-vs-`pes` shakedown of the full
`spec/LIVE-RUNS.md` pipeline, doubling as the multi-seed replication that
[[PES Stage 0 vs Null — Circle Packing Comparison — 2026-07-08]] flagged as the
required next step before any Stage 1/2 PES work — that comparison is N=1 (one
seed, one benchmark, one run per arm) and its own "Sequencing implication"
section says so explicitly. Also carries
[[tasks/0031-investigate-pes-no-diff-rate-and-plateau]]'s instrumentation ask
(the elevated no-diff rate observed in that N=1 run) per INDEX.md's "Next" list.

## Preflight

Agent-side (no cluster access needed) — evidence pasted below, checked at the
time this ticket was drafted (2026-07-09); **must be re-verified immediately
before launch**, since the repo has moved since (see commit-hash caveat):

- [x] `loop/guardrails/verify.sh` green — `python3 -m unittest discover tests`:
      **104/104 passed**, 0 failures, run at commit `5f7899d` on
      `task/0024-noema-standalone-repo`. **Caveat**: the working tree had other
      uncommitted/staged changes from a parallel agent session at the time this
      ticket was drafted (`git status --short` showed `M .gitignore`, `A
      CLAUDE.md`, `A Makefile`, `A spec/LIVE-RUNS.md` among others) — re-run
      `verify.sh` and re-paste the commit hash actually being launched
      immediately before Launch, don't trust this snapshot.
- [ ] **Config: `coordination.module` is the ONLY diff between paired arm
      configs** — structurally true by construction: `run_noema_arm.py` builds
      one `NoemaConfig` from the same CLI flags for both arms in a pair, only
      `--arm` differs, and `CoordinationConfig(module=args.arm)` is the only
      config field that reads it. **Not yet verified by an actual `diff`
      output** because no frozen config file exists to diff (see note below) —
      this bullet cannot be fully closed until either
      [[tasks/0041-persist-frozen-run-config-with-hash]] lands or someone
      manually diffs two constructed `NoemaConfig.__dict__`s by hand before
      launch. Prompt-config constants to confirm match `spec/STUDY.md`'s locked
      design before launch: `num_top_programs=1` ✓ (script default),
      `num_inspirations=0` ✓ (script default), `include_artifacts=False` ✓
      (script sets `PromptConfig(include_artifacts=False, ...)`),
      `use_template_stochasticity=False` ✓ (script sets it explicitly). Model
      pin (`Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf`, `-ngl 99 --ctx-size 10240
      -np 1`, no `-ctk`/`-ctv`) matches `spec/STUDY.md`'s "Held constant" model
      row — confirm the actual node's `llama-server` flags at launch time (user
      preflight bullet below), don't assume from a prior session.
- [ ] **Budget in config == `budget_tokens` in ticket** — **NOT yet true as
      the script stands.** `run_noema_arm.py`'s current defaults are
      `BudgetConfig(total_tokens=2_000_000)` plus `--iterations 50` (an
      iteration cap), which is what produced
      `noema_pes_stage0_output/` — this ticket's nominal reproducibility basis.
      `spec/STUDY.md` (signed off *after* that run) requires **1,000,000
      tokens/run, ending on `BudgetExhausted`, never on iteration count**. This
      ticket adapts the basis rather than copying it verbatim: launch commands
      below pass `--iterations` high enough to be a non-binding safety cap
      (10,000) so `BudgetExhausted` is what actually stops each run, and rely
      on `NoemaConfig(budget=BudgetConfig(total_tokens=1_000_000))` — **the
      script has no `--budget-tokens` CLI flag today**, so this requires either
      a one-line pre-launch edit to `run_noema_arm.py`'s hardcoded
      `BudgetConfig(total_tokens=2_000_000)` (bump to 1,000,000) or an inline
      monkeypatch. Flagging rather than silently fixing: **this ticket does not
      authorize a code change**; whoever executes Launch must apply this
      one-line edit as a tracked commit (or add the CLI flag properly) before
      running, not as an unreviewed local diff.
- [x] **Benchmark evaluator subprocesses the candidate program** —
      `examples/circle_packing/evaluator.py:142-143`:
      `subprocess.Popen([sys.executable, temp_file_path], stdout=subprocess.PIPE,
      stderr=subprocess.PIPE)`, with a `subprocess.TimeoutExpired` handler at
      line 172. Confirmed by direct grep, not assumed.
- [x] **Run-dir naming**: none of the six run dirs below exist yet (checked
      2026-07-09): `examples/circle_packing/runs/RT-0002-null-s42/`,
      `.../RT-0002-pes-s42/`, `.../RT-0002-null-s43/`, `.../RT-0002-pes-s43/`,
      `.../RT-0002-null-s44/`, `.../RT-0002-pes-s44/`. `run_noema_arm.py`'s
      `--output-dir` flag must be pointed at these paths explicitly (its own
      examples in the runbook use flatter names like `noema_pes_output` — do
      **not** reuse those, they'd collide with prior experiment data, which is
      never to be overwritten).

Preflight (user-side / supervised session, needs cluster — **not** agent-
executable, listed here only so Launch has the checklist in one place):

- [ ] `cluster.py status`: chosen nodes idle; never repurpose a server not
      started this session (`ps -o etimes=`)
- [ ] Model file present on each node (`ls /var/tmp/models/`); server flags
      match `spec/STUDY.md` pins exactly
- [ ] Tunnels up: `curl -sf localhost:<port>/health` per node, for all 6 ports
      above (or however many nodes this session actually uses in parallel —
      see node-count note below)

**This ticket has two upstream blockers and is not launch-ready as filed**:

1. **RT-0001** (the metering smoke run) — per INDEX.md "Blocked / queued for
   the user," RT-0001 clears the `ledger-completeness-live` standing goal and
   is the first live confirmation that metering holds under real infra since
   task 0025's fix. RT-0002 should not be the first live run to exercise the
   post-0025 ledger.
2. **Task 0038** (`loop/scripts/verify-run.sh`) — Now #1 in INDEX.md,
   explicitly "gates RT-0001 and every ticket after it." This ticket's own
   post-run verification step (below) assumes `make verify-run` exists; until
   0038 lands, post-run verification would have to be done by hand against the
   `spec/LIVE-RUNS.md` §4 checklist directly, which is slower and more
   error-prone for 6 runs at once.

Status stays `queued` until both land; this ticket is prep, not a launch
authorization.

## Launch

**Node count**: the six runs above can share as few as 2 nodes (run seed-pairs
sequentially, one `null`+`pes` node-pair per seed, ~2.6h/run × 3 sequential
pairs ≈ 8h wall-clock) or use up to 6 nodes for full parallelism (~2.6h total if
the lab has 6 idle machines — check `cluster.py status` first, per
[[Distributed Inference Cluster]]'s "spare capacity: 7 idle machines" note, not
guaranteed available). Commands below assume the sequential-pair pattern (2
nodes, reused across 3 seed batches) since it's the lower-risk default; adapt
node/port numbers if running more in parallel.

```bash
# --- one-line pre-launch fix required (see Preflight budget bullet) ---
# Edit examples/circle_packing/run_noema_arm.py:
#   BudgetConfig(total_tokens=2_000_000)  ->  BudgetConfig(total_tokens=1_000_000)
# Commit this as its own tracked change before launching, not an ad hoc local diff.

# --- per seed-pair (repeat for seed in 42 43 44) ---
cd ~/distributed-inference
./.venv/bin/python cluster.py status   # confirm target nodes idle

ssh uni-<node-a> "nohup env LD_LIBRARY_PATH=/home/cq25988/bin /home/cq25988/bin/llama-server \
  --model /var/tmp/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf \
  -ngl 99 --host 0.0.0.0 --port 8080 --ctx-size 10240 -np 1 \
  </dev/null >/var/tmp/llama-server.log 2>&1 & disown; echo started"
ssh uni-<node-b> "nohup env LD_LIBRARY_PATH=/home/cq25988/bin /home/cq25988/bin/llama-server \
  --model /var/tmp/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf \
  -ngl 99 --host 0.0.0.0 --port 8080 --ctx-size 10240 -np 1 \
  </dev/null >/var/tmp/llama-server.log 2>&1 & disown; echo started"

ssh -N -L 8090:localhost:8080 uni-<node-a> &   # null
ssh -N -L 8091:localhost:8080 uni-<node-b> &   # pes
until curl -sf --max-time 3 http://localhost:8090/health >/dev/null 2>&1; do sleep 2; done
until curl -sf --max-time 3 http://localhost:8091/health >/dev/null 2>&1; do sleep 2; done

cd /root/noema-evolve
nohup .venv/bin/python examples/circle_packing/run_noema_arm.py \
  --arm null --api-base http://localhost:8090/v1 \
  --output-dir examples/circle_packing/runs/RT-0002-null-s<SEED> \
  --seed <SEED> --iterations 10000 \
  > examples/circle_packing/runs/RT-0002-null-s<SEED>.log 2>&1 &

nohup .venv/bin/python examples/circle_packing/run_noema_arm.py \
  --arm pes --api-base http://localhost:8091/v1 \
  --output-dir examples/circle_packing/runs/RT-0002-pes-s<SEED> \
  --seed <SEED> --iterations 10000 \
  > examples/circle_packing/runs/RT-0002-pes-s<SEED>.log 2>&1 &
```

`--iterations 10000` is a non-binding safety cap only — at ~6.3k tokens/iteration
(circle_packing's PES iterations run heavier per
[[PES Stage 0 vs Null — Circle Packing Comparison — 2026-07-08]], ~9k
tokens/iteration once planning+reflection are both firing) a 1M-token budget
exhausts at roughly 110-160 iterations for `null` and 55-110 for `pes`, both far
under 10,000; `BudgetExhausted` is expected to be what actually ends each run.

## Watch

Per run, mid-session (user's discretion; nothing automated acts on a live run
per `spec/LIVE-RUNS.md` §3):

```bash
grep -c "exceed_context_size_error" <log>          # must stay 0
grep -c "no valid program in LLM response" <log>    # format-compliance failures;
                                                     # compare null vs pes rate —
                                                     # this is 0031's open question
grep -oP "combined_score=\K[0-9.]+" <log> | awk '$1>0'
grep "New best program" <log>
grep -c "PES planning call failed" <log>            # pes only — should stay ~0;
                                                     # if not, see the plan-failure
                                                     # lineage-loss risk noted in
                                                     # [[tasks/0042-fix-pes-lineage-loss-on-plan-failure]]
```

## Teardown

Only kill what this session started (`ps -o etimes=` against launch time first):

```bash
ssh uni-<node-a> 'pkill -f "[l]lama-server"'   # if started this session
ssh uni-<node-b> 'pkill -f "[l]lama-server"'   # if started this session
pkill -f "ssh -N -L 8090:localhost:8080"
pkill -f "ssh -N -L 8091:localhost:8080"
# ...repeat tunnel-kill for 8092-8095 if run in parallel
ssh uni-<node-a> 'nvidia-smi --query-gpu=memory.used --format=csv'   # confirm idle
ssh uni-<node-b> 'nvidia-smi --query-gpu=memory.used --format=csv'
```

Repeat Launch → Watch → Teardown for seeds 43 and 44 (or run all three
seed-pairs in parallel across 6 nodes if the lab has capacity — check
`cluster.py status` first).

## Post-run (agent, after status: launched -> the runs complete)

Per `spec/LIVE-RUNS.md` §4/§5, once `make verify-run` exists (task 0038):

```bash
make verify-run DIR=examples/circle_packing/runs/RT-0002-null-s42 TICKET=RT-0002
make verify-run DIR=examples/circle_packing/runs/RT-0002-pes-s42  TICKET=RT-0002
# ...for all 6 run dirs
```

Each verified run gets one row in `loop/runs/ledger.tsv`; this ticket moves to
`loop/runs/done/`; a vault run note documents the 3-seed result (does the
Stage-0 +8.7% direction hold, per-seed spread, updated no-diff-rate comparison
per 0031) and is linked back to
[[PES Stage 0 vs Null — Circle Packing Comparison — 2026-07-08]] and
[[PES Phase 2 Plan]] (whose Stage 1/2 case this replication is meant to
confirm or undercut).
