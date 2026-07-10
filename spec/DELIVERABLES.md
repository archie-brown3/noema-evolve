# DELIVERABLES SPEC (DRAFT v2, 2026-07-10) — goals, claims, and the study design that carries them

> Status: **draft, awaiting user sign-off.** Supersedes the misnamed draft
> `spec/LOOP.md` (v1, same day). On sign-off, §3 becomes STUDY.md v2
> (contract-tier edit). Until then `spec/STUDY.md` v1 remains canonical.
> §4 records what the 2026-07-10 research pass resolved, with vault citations.
> Loop mechanics live in `loop/` and `spec/LOOP-AUTONOMY.md`, not here (§5).

## 1. Aims — the claims the dissertation defends

- **C1 (framework/methodology)**: a controlled ablation of coordination in
  LLM-driven evolutionary search is only valid under prompt identity, metering
  integrity, and determinism — and noema enforces all three by construction.
  The literature budgets by iteration count; noema's equal-token-budget basis
  is itself a methodological contribution.
- **C2 (headline, interaction)**: coordination mechanisms and population
  substrates **interact** at equal token spend — a mechanism's value is
  contingent on the state topology the substrate creates. Directional,
  pre-registered predictions in §3.5.
- **C3 (program quality)**: with a capable cloud model held identical across
  arms, the best evolved programs approach published baselines
  (AlphaEvolve/OpenEvolve reference numbers on the same benchmarks).
- **C4 (transfer)**: effect *directions* measured at local-14B scale do / do
  not predict effect directions at cloud-model scale (either answer is a
  finding; this is what makes Phase L load-bearing science).

## 2. Deliverables

| id | deliverable | done-when (machine-checkable) |
|---|---|---|
| D-FRAME | Validated framework: triad enforced, run protocol executable | verify.sh green; LIVE-RUNS §4 passes on a smoke ticket; frozen-config artifact per run (task 0041) |
| D-LOCAL | Phase-L matrix (§3): mechanism × substrate at local scale | every cell has 3 verified run dirs; interaction stats by committed script |
| D-CLOUD | Phase-C matrix: headline quality on one cloud model | every cell has 3 verified run dirs; best-program table vs published baselines by script |
| D-ANALYSIS | Stats + figures: interaction plots, budget trajectories, per-seed scatter | regenerate from run registry by one command; no hand-edited numbers |
| D-THESIS | Chapter feeds: decision log, evidence notes, deviations register | Decisions.md rows cover every sanctioned change; every evidence note cites verified run dirs |

Every vault task names one of these in `serves:`, or is a `chore`.

## 3. The study design (STUDY.md v2 PROPOSAL)

### 3.1 Definitions (sharpened 2026-07-10)

- **Mechanism** = closed-loop coordination: module-private state updated from
  evaluation outcomes, adapting injected guidance.
- **Substrate** = open-loop machinery: population store, parent selection,
  prompt construction, operators. Substrate selection may be a *memoryless
  function of population state* (UCT's Q/N included); mechanisms hold
  module-private state and shape prompts. (Record this boundary in
  Decisions.md before the freeze — examiner-proofing, vault: Substrate Axis
  Recommendation §risk 3.)
- **s1 lineage prompting is substrate** (deterministic function of ancestry,
  no outcome-updated state). Task 0035 re-scoped to a substrate toggle, OFF in
  matrix cells, available as a supplementary ablation.

### 3.2 Mechanism axis (5 levels)

| level | shape | token price |
|---|---|---|
| `null` | nothing | 0 |
| `hifo` | population-level hindsight pool + foresight regime | amortized (~1 extraction/tick) |
| `pes-faithful` | LoongFlow-faithful: plan-led prompt, paper constants — **reference arm / validity anchor, explicitly not the contribution** | high (per-mutation plan + reflect) |
| `pes-custom` | our refinement: retry-integrated (0049/0050), reflection-seeded retries — the contribution; faithful-vs-custom is itself a result | high |
| `bandit` | AsymmetricUCB over the operator menu, cost-blind first | 0 (no coordination calls) |

Research outcomes folded in (2026-07-10, vault-cited):
- **EoH: no arm.** Rejection re-examined and strengthened — population-mediated
  feedback only; its closed-loop upgrades already exist as hifo (thought layer,
  extracted once already) and bandit (allocation over the 0027 menu).
  Consequence: **task 0027 (operator menu) is load-bearing for the bandit arm**
  — without it the bandit routes over {diff, rewrite} only.
  [[EoH Mechanism Re-examination — 2026-07-10]]
- **ReEvo: one distinct piece survives** — contrastive reflection against the
  island-best exemplar; memoryless, completes the token-price ladder
  (s1≈0 < hifo < reevo ≈ pes-custom < pes-faithful; "pes-lite"/"pes-full" are
  retired colloquial aliases for pes-custom/pes-faithful — Decision #26, never
  config keys). Weakest source evidence of any
  candidate (Δ0.06/0.09) → **backlog (task 0043), candidate 6th arm for
  Phase C only if Phase L runs clean.** [[ReEvo Fit Assessment]]
- **Ensemble arm (bandit over modules): dropped from the matrix.** Premise
  refuted (modules do advise every turn) but conclusion confirmed and
  sharpened: module state is cadence-coupled to the pull sequence, so an
  ensemble selects among *degraded variants* of the arms — construct-validity
  break. Stretch-only design documented.
  [[Ensemble Arm Feasibility Analysis — 2026-07-10]]

### 3.3 Substrate axis (2 levels + 1 gated probe)

- **islands + MAP-Elites** (incumbent, anchor level): wide migration-mixed
  fronts, broken lineages.
- **global-tree + UCT** (task 0037, the one real build): deep persistent
  lineages; strongest published structure evidence in the corpus
  (MCTS-AHD ICML 2025 §5.2).
- **Probe 1 — thoughts (gated, +6 runs)** *(signed off, Decision #20)*: EoH
  thought/code co-evolution ON ({null, hifo} × islands × 3 seeds; thoughts-OFF
  cells reused from the matrix). Largest published substrate effect in the corpus
  (EoH Table 5: 0.66% vs 150.89% code-only collapse; Table 6 interaction study),
  and it tests whether hifo's transplanted mechanism depends on its native
  thought-bearing substrate — a documented fidelity deviation and the matrix's
  least-protected cell. Machinery ships with task 0027; thoughts stay OFF in
  every matrix cell.
- **Probe 2 — Boltzmann (gated, +6 runs, first cut if schedule slips)** *(signed
  off, Decision #20)*: LoongFlow's sampler on the islands store, {null,
  pes-faithful} × 3 seeds — decomposes store-topology vs sampling-policy if a
  substrate main effect appears; yields `pes-faithful × boltzmann` as the
  closest-to-LoongFlow fidelity anchor.
- **Cut** *(signed off, Decision #21)*: SA+Boltzmann (dominated), flat panmictic
  (null row already answers it), ShinkaEvolve sampling (license + dominated),
  BaSE trajectory allocation (**closed-loop → wrong axis by our own definition**;
  prompt-identity violation in faithful form; 0045 stays feasibility-only).
  [[Substrate Axis Recommendation — 2026-07-10]]

### 3.4 Matrix, budget, phases

- **Phase L (local)**: 5 mechanisms × 2 substrates × circle packing × 3 seeds
  = **30 runs ≈ 78 node-hours**, Qwen2.5-Coder-14B, 1M tokens/run, behind the
  baseline-quality gate (null arm reproducible across 3 seeds before any
  matrix ticket is cut). Feeds C1, C2 (local), C4.
- **Phase C (cloud)**: same matrix on one cloud model identical across arms,
  3 seeds, on circle packing + **bin packing (decided in, 2026-07-10)** +
  a third benchmark (recommended: TSP — ships with OpenEvolve, published
  numbers in EoH/EvoTune; final pick at Phase-C sign-off). Full cross = 90
  runs; trim options (third benchmark on islands only, or on the two best
  mechanisms) sized at Phase-C sign-off. Feeds C2 (headline), C3, C4.
- **Registered metrics (pre-registration closes at the config freeze)**: the
  STUDY v1 metric list PLUS best `combined_score` vs **tokens spent** AND vs
  **evaluation count** — the second axis makes noema trajectories directly
  overlayable on ShinkaEvolve Fig. 5 and AlphaEvolve-style eval-count
  reporting. Both axes from run-dir artifacts only. Nothing may be added
  after unblinding.
- Prompt skeleton change (coordination block precedes task section for ALL
  arms; null gets an empty block) is contract-tier and re-bases every prompt
  test — one commit, triad tests extended in the same commit.
- **Operator menu (task 0027)**: promoted to headline path as bandit
  infrastructure (Decision #23). The 5-operator menu (e1/e2/m1/m2/m3) ships
  opt-in; headline matrix configs keep thoughts OFF; the bandit arm routes
  over this menu. Thoughts probe (Decision #20) tests them separately.

### 3.5 Pre-registered interaction predictions (falsifiable)

1. `pes-*` gain more under tree than islands (largest positive interaction —
   the sampler stops abandoning the lineages their lessons live on).
2. `hifo` ≈ substrate-insensitive (pool reads the population surface both
   stores expose). Least-protected cell: hifo has no published ablation, so an
   interaction here is a novel measurement either way.
3. `bandit`: weak positive rewrite-payoff shift under tree (deep lineages
   accumulate structural commitment).
4. `null` row: tree > islands on late-run best at equal budget (MCTS-AHD §5.2
   signature); this row is the substrate main effect.

No metric or prediction added after unblinding.

### 3.6 Carried unchanged from STUDY v1

Budget semantics (1M shared pool, per-account metering, BudgetExhausted end),
guarantee triad + tests, metrics list, Friedman + Wilcoxon (n=3 flagged
indicative), live-run ticket gating (user launches), MLX track out of scope
(task 0052).

## 4. Sign-off register

**Decided by the user 2026-07-10** (recorded, not open): seeds 5→3;
s1 → substrate toggle; bandit-over-operators into the menu; PES pair;
two-phase cloud-canonical; both metric axes (tokens AND evaluations);
bin packing IN for Phase C, and not the only additional benchmark;
**bandit-over-MODELS as a Phase-C arm vs the default single-model arm —
backlog, after the Phase-L matrix is set up (task 0053)**.

**Decided by the user 2026-07-10** (research-recommended, signed off):
(a) EoH no-arm (Decision #15);
(b) ReEvo → backlog, Phase-C candidate 6th arm (task 0055, Decision #16);
(c) ensemble-over-modules dropped (Decision #17);
(d) probe order: thoughts first, Boltzmann second, both gated; Boltzmann
first cut if schedule slips (Decision #20);
(e) BaSE off the substrate axis entirely (Decision #21);
(f) §3.1 mechanism/substrate boundary wording recorded in Decisions.md
(Decision #22);
(g) task 0027 (operator menu + thought machinery) promoted onto the headline
path as bandit infrastructure, thoughts OFF in matrix cells (Decision #23);
(h) third Phase-C benchmark = TSP (Decision #24).

**Still open, user-only**: **Q4** Phase-C model + spend cap + per-run token
budget (also gates the equal-token vs equal-dollar question task 0053 must
answer) — converted to backlog task 0056.

## 5. How work gets done (pointer, not policy)

The loop (`loop/`, `spec/LOOP-AUTONOMY.md`, `loop/contract.md`) prepares
everything and launches nothing. Task discipline: every task `serves:` a
§2 deliverable or is a chore; context frozen at creation; non-obvious choices
append a Decisions.md row. The loop is infrastructure — it appears nowhere in
the dissertation and no claim depends on it.

## Changelog

- 2026-07-10 (v2.2): user signed off (d)–(h) from §4 sign-off register.
  Probe order locked (thoughts first, Boltzmann second); BaSE cut from
  substrate axis; mechanism/substrate boundary recorded (Decision #22);
  task 0027 promoted to headline path as bandit infrastructure; TSP added
  as third Phase-C benchmark. Q4 converted to backlog task 0056.

- 2026-07-10 (v2.1): user decisions folded in — both metric axes
  registered; thoughts probe promoted over Boltzmann; bin packing decided in
  + third-benchmark intent; model-routing bandit arm added to backlog
  (task 0053); sign-off register restructured into decided / awaiting /
  open.

- 2026-07-10: v2 — reframed from the misnamed LOOP.md around
  goals/claims/deliverables; four research analyses folded in (EoH, ReEvo,
  ensemble cadence, substrate axis — vault notes linked in §3).
