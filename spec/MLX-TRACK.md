# MLX-TRACK: KernelBench-style evaluation for evolutionary coordination arms

> Grounded in `examples/mlx_metal_kernel_opt/`. Independent of the main matrix
> (DELIVERABLES.md); this is a supplementary track that tests the same arms on a
> qualitatively different task — GPU kernel writing — using the KernelBench
> `fast_p` formula. The question: *which components of evolutionary search help
> write GPU kernels?*

## 1. Task — Metal kernel optimization for Qwen3 GQA

- **Domain**: Grouped Query Attention (GQA) on Apple Silicon, Qwen3-0.6B-bf16.
- **Target**: Evolve the Metal kernel source inside `initial_program.py`'s
  `EVOLVE-BLOCK-START..EVOLVE-BLOCK-END` region to outperform MLX's
  `mx.fast.scaled_dot_product_attention`.
- **Constraints**: Kernel signature, template params, thread-grid mapping, and
  bounds checks must not change. Only the computation body evolves (memory
  access patterns, algorithm structure, SIMD vectorization, GQA-specific
  indexing).
- **Current baseline result** (post-validity-fix, 25 iterations): best evolved
  kernel is **3.2% slower** than MLX baseline. Evolution never breached parity.

## 2. Evaluator — what it measures (current)

| Stage | Mechanism |
|-------|-----------|
| Extraction | `exec()` in protected environment; syntax validation |
| Correctness | 4 bfloat16 test cases (L=8,16,32,64); shape + finiteness + statistical sanity; mean across cases; gate at 0.90 |
| Metal compile | bf16-incompatible kernels fail immediately (no retry) — 32% failure rate |
| Baseline | 4 benchmark configs via `mlx_lm.generate` with standard Attention |
| Custom benchmark | Same 4 configs with monkey-patched `CustomGQAAttention`; subprocess propagation |
| Score | `combined_score = decode_pct×3 + mem_bonus + consistency×10 + correctness×5 + safety×5 - error_penalty`; gated to -1000 on any failure |

Core flaw: opaque composite, not a speedup ratio. Per-benchmark metrics
available but discarded by MAP-Elites selection.

## 3. Improvement direction — KernelBench `fast_p` formula

KernelBench (Ouyang et al., ICML 2025): $$\text{fast}_p = \frac{1}{N} \sum_{i=1}^{N}
\mathbb{1}(\text{correct}_i \land \text{speedup}_i > p)$$ where `speedup_i =
T_torch,i / T_kernel,i`, correctness = compilation + reference match on 5
randomized inputs.

| Property | `combined_score` | `fast_p` |
|----------|-------------------|----------|
| Interpretability | "2.96" — opaque | "0.42 of kernels beat baseline" |
| Actionability | Cannot guide mutation | "Speedup: 0.85x, need >1.0x" |
| Correctness decomposition | Buried in composite | `fast_0` isolates correctness rate |
| Optimization decomposition | Confounded | `fast_1.5` / `fast_2` isolates genuine speedup |

Speedup sweep disambiguates failure modes: low `fast_0` → compilation bottleneck;
high `fast_0`, low `fast_1` → kernels correct but slower; high `fast_1`, low
`fast_2` → wins are marginal noise; high `fast_2` → genuine improvement.

## 4. Adapting `fast_p` to evaluate evolutionary arms

Protocol: for each arm, for each of N problems, run an independent evolutionary
search with a fixed token budget. From the final population, take the best
kernel by wall-clock time among those passing correctness. Compute `fast_p` over
N problems per arm.

| Arm | Coordination | Per-mutation cost | Hypothesis |
|-----|-------------|-------------------|------------|
| `null` | None | 0 coordination tokens | Pure LLM capability baseline |
| `hifo` | Insight pool + regime nav | ~1 extraction/tick | Cross-problem pattern extraction (tiling, coalescing, SIMD width) |
| `pes` | Plan → execute → reflect | Plan + reflect/mutation | Lineage-specific causal debugging (why did this register layout fail?) |

Arm × kernel hypotheses: (1) PES raises `fast_0` — structured plans force bf16
compatibility checks before code generation. (2) HiFo raises `fast_1.5` —
cross-problem insight pooling surfaces general optimization patterns. (3) Null
is the lower bound. (4) Arm × substrate: PES gains more under tree (persistent
lineages preserve causal chains); HiFo is substrate-insensitive (pool reads
population surface regardless of store topology).

## 5. What is ablated — prompt content only

The host loop is identical across arms. Coordination modules change only what
text is injected: null → nothing; hifo → insight tips + regime directive; pes →
structured plan + causal reflection on retry. Substrate, parent selection,
evaluation, mutation operators are invariant.

## 6. Grounding in the MLX example

The MLX example instantiates this track for a single problem (Qwen3 GQA). Scaling
to KernelBench means: replace Qwen3 with KernelBench Level 1–2 problems (~200);
replace the MLX evaluator with binary correctness + direct speedup ratio; run
per-problem evolutionary searches; compare `fast_p` across arms at equal token
budget.

This track tests the transfer hypothesis (DELIVERABLES C4): do arms that help
on circle-packing/TSP/bin-packing also help on GPU kernel writing? A divergence
in rankings across domains is itself a finding.

## 7. Design decisions

- p ∈ {0, 1, 1.5, 2} — mirrors KernelBench's standard sweep.
- Token budget identical across arms. PES gets fewer mutation attempts — this
  is intentional: tests whether coordination tokens beat additional mutations.
- One-shot prompt preserved; coordination advice appended as suffix (per
  noema's `inject_advice`).
- bf16 Metal syntax guidance in prompts — the 32% compilation failure rate
  signals an evaluator-agnostic improvement.