# STUDY SPEC — coordination-mechanism ablation (noema)

> Signed off by the user 2026-07-09 (structured Q&A, this document is the record).
> Changes to anything under "Locked" are contract-tier: queue for the user.
> Vault context: [[Noema Architecture]], [[Tree Substrate Plan]],
> [[Next Mechanism Decision]], [[Distributed Inference Cluster]],
> [[Running Noema Arm Comparisons on the Lab Cluster]].

## Research question

Does an explicit coordination mechanism — a layer that observes search state and
injects guidance into mutation — buy measurable search performance in LLM-driven
evolutionary program search, at **equal token spend**, relative to the same
substrate with no coordination?

## Locked design

### Factors

| Factor | Levels | Status |
|---|---|---|
| mechanism | `null`, `hifo`, `pes`, `s1` (lineage prompting) | headline axis; `s1` to implement (task 0035) |
| structure | islands+MAP-Elites (headline) vs global-tree+UCT | **staged pilot** (D1): tree runs OFF-vs-OFF pilot only; crosses into the matrix only if the pilot gate (below) passes |
| benchmark | circle packing; online bin packing | bin packing to port (task 0036) |
| seeds | 42, 43, 44, 45, 46 (5 per cell) | — |

The mechanism set spans the design space deliberately: `null` (nothing),
`hifo` (paid prompt guidance, hindsight pool), `pes` (paid prompt guidance,
per-mutation planning), `s1` (free prompt guidance, ancestry-derived — zero
coordination tokens). The AsymmetricUCB bandit (selection-level, budget-aware)
is the documented next candidate if an arm is added later — see
[[Next Mechanism Decision]] sign-off record.

### Held constant across every run (the substrate)

- OpenEvolve pinned at `80945ed` (v0.2.27), adapters in `noema/substrate/` only.
- Mutation menu: **diff/rewrite only** on the headline path. The EoH 5-operator
  menu (task 0027) lands opt-in and stays OFF in headline configs.
- Model: **Qwen2.5-Coder-14B-Instruct-Q4_K_M**, single llama.cpp node per arm,
  flags `-ngl 99 --ctx-size 10240 -np 1`, **no KV-cache quantization**.
- Prompt config: `num_top_programs=1`, `num_inspirations=0`,
  `include_artifacts=false` (ctx-overflow fix, 2026-07-08). Any change re-bases
  the whole matrix — treat as contract-tier.
- Benchmark programs use the role-structured layout (`F_imm` outside
  EVOLVE-BLOCK, `F_mut` inside — Tree Substrate Plan Phase A), applied
  identically to every arm and both structures, before the config freeze.

### Budget

**1,000,000 tokens per run** (shared pool; mutation + coordination accounts —
coordination spend displacing mutation spend is the experimental point).
Calibration basis: 6,313 tok/iter, ~17 tok/s ⇒ ≈158 iterations, ≈2.6 h/run.
Runs end on `BudgetExhausted` with a final checkpoint, never on iteration count.

### Matrix and cluster cost

- Headline: 4 mechanisms × 2 benchmarks × 5 seeds = **40 runs ≈ 104 node-hours**
  (~7 supervised sessions at 6 parallel nodes).
- Tree pilot: {islands, tree} × OFF × circle packing × 3 seeds = **6 runs ≈ 16 h**.
  (3 seeds, not the plan's 2 runs: every N=1 comparison this project has run was
  later invalidated — single-island bug, runner mismatch. Deviation logged.)
- Tree pilot **gate** (full 2×4 cross only if it passes, +40 runs):
  median best-score delta between structures exceeds the within-arm seed spread
  (max−min), or the MCTS-AHD §5.2 signature is visible (tree's best-score slope
  over the final third of the budget exceeds islands', i.e. late improvement
  after islands plateau). Gate evaluation is a written comparison note; the
  expansion decision queues for the user.

### Metrics (per run, from run-dir artifacts only)

1. Primary: best `combined_score` at budget exhaustion (elite).
2. Population mean `combined_score` over valid programs (both MUST be reported —
   research-constraints).
3. Process: valid-program rate, no-diff/format-failure rate, tokens per
   improvement, coordination-account share of spend.
4. Search-shape: per-island (or per-subtree) best, best-score trajectory vs
   tokens spent.

### Statistics

Friedman test across mechanisms per benchmark (n=5 seeds); Wilcoxon signed-rank
post-hoc vs `null` with Holm correction; report effect sizes and per-seed
scatter, not just p-values. N=5 is flagged "indicative" in all writing
(research-constraints). No metric may be introduced after unblinding a
comparison — metrics above are the pre-registration.

## Validity guarantees (the triad, restated for two axes)

1. **Prompt identity**: within a structure, arms differ only in
   `coordination.module`; shared prompt prefix byte-identical across arms
   (existing test). Across structures, the structure factor may change *which*
   parent is selected, never *how* the prompt is built (Tree Plan B5 test).
2. **Metering integrity**: every LLM call metered per-attempt from
   `response.usage`; `llm_calls.jsonl` has zero `total_tokens: null` rows;
   ledger total equals hand-summed log total exactly.
3. **Determinism**: fixed seed per run; config diff between paired arms shows
   `coordination.module` (and nothing else) as the delta.

Every headline claim in the dissertation must trace to a run dir that passed
post-run verification (spec/LIVE-RUNS.md §4).

## Out of scope (explicit)

- MLX Metal kernel optimization: **stretch research track**, Mac-only, own
  mini-spec (`spec/MLX-TRACK.md`, not yet written); never a matrix cell.
- Cloud/API runs: not canonical; optional robustness spot-check only.
- Adaptive scheduling over the 5-operator menu (the bandit/softmax arms).
- Substrate #3, mechanism #5, benchmark #3 — anything not named above.

## Milestones (deadline: early September 2026)

| Week | Exit criterion (measurable) |
|---|---|
| W1 (–Jul 19) | metering fix merged; smoke run ticket RT-0001 verified (ledger goal passes); shakedown ticket RT-0002 (3-seed null-vs-pes) launched |
| W2 (–Jul 26) | s1 arm merged (tests incl. hand-computed traces); bin packing ported (role-structured, eval subprocessed); **config freeze commit tagged** |
| W3–W4 (–Aug 9) | headline matrix complete: 40 verified run dirs |
| W5 (–Aug 16) | tree pilot complete + gate note written; stats + EvoReplay analysis of headline matrix |
| W6 (–Aug 23) | full-cross decision executed (run or documented-skip); results chapter drafted |
| W7–W8 (–Sep 6) | writing buffer; MLX track only if W1–W5 all green |

## Study-level done-when

- [ ] 40 headline run dirs, each passing post-run verification (LIVE-RUNS §4)
- [ ] Tree pilot gate note written with the expansion decision recorded
- [ ] Friedman + Wilcoxon results table (elite AND population-mean) generated
      from verified runs only, by script, committed with the run registry
- [ ] Every deviation from this spec logged in this file's changelog section

## Changelog

- 2026-07-09: created; four sign-offs recorded (D1 staged, arm#4 = s1,
  0027 approved off-headline, budget = 1M).
