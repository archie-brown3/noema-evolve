"""Online bin packing benchmark (FunSearch/EoH formulation).

Items arrive one at a time and are placed immediately — no reordering. The only
evolvable part (F_mut, inside the EVOLVE-BLOCK) is the heuristic that scores the
open bins for the arriving item; the harness places the item in the highest-
scoring bin that fits, or opens a new one. This is deliberately *online*: an
offline heuristic (e.g. First-Fit-Decreasing) is within 11/9 of optimal and
leaves no room to evolve, which is why the earlier offline benchmark scored 1.0
on the trivial program (task 0036 finding). Online is where the per-bin heuristic
matters and where FunSearch/EoH publish a real gap to optimal (task 0091).

The initial heuristic is best-fit; a better evolved heuristic packs into fewer
bins, lowering the mean excess over the lower bound and raising the score.
"""

import numpy as np


# ENTRY POINT
# F_imm: the I/O contract the evaluator relies on. Generates the fixed Weibull
# instance set (Decision #6), runs the ONLINE packer with the evolvable priority
# heuristic, and returns (bins_used, lower_bound) per instance. Its signature and
# return shape must not change under mutation.
def run_bin_packing(seed=42):
    results = []
    for instance_seed in INSTANCE_SEEDS:
        items = generate_instance(instance_seed)
        bins_used = online_pack(items, BIN_CAPACITY, priority)
        lower_bound = int(np.ceil(sum(items) / BIN_CAPACITY))
        results.append((int(bins_used), lower_bound))
    return results


# task 0096: NOT called by the per-iteration evaluator — run_bin_packing above
# is the search-loop entry point and is unchanged. This is the final-reporting
# path: Decision #6's full n x capacity matrix, on the held-out seed range
# (6-10, disjoint from the scored set's 1-5), for comparability to published
# FunSearch/EoH numbers at scale. Uses whatever `priority` evolution produced.
def run_bin_packing_held_out():
    results = {}
    for n_items, capacity in HELD_OUT_CONFIGS:
        config_results = []
        for instance_seed in HELD_OUT_SEEDS:
            items = generate_instance(instance_seed, n_items=n_items, capacity=capacity)
            bins_used = online_pack(items, capacity, priority)
            lower_bound = int(np.ceil(sum(items) / capacity))
            config_results.append((int(bins_used), lower_bound))
        results[(n_items, capacity)] = config_results
    return results


# HELPER FUNCTIONS
# F_imm: the fixed benchmark definition — the Weibull item distribution and the
# ONLINE arrival loop. Not evolvable: the LLM changes only how bins are scored,
# never the item distribution, the instance set, or the no-reordering rule.
BIN_CAPACITY = 100
N_ITEMS = 1000
# A committed, seeded instance set (no runtime downloads). This is the SCORED
# set — evaluated every iteration during search, kept at Decision #6's
# smallest size so search throughput is unaffected. Unchanged by task 0096;
# existing arm results at this size remain comparable.
INSTANCE_SEEDS = (1, 2, 3, 4, 5)

# task 0096: Decision #6 specifies Weibull n in {1000, 5000, 10000} and
# capacity in {100, 500}; only n=1000/C=100 was committed. HELD_OUT_CONFIGS
# covers the full matrix for final-reporting comparability to FunSearch/EoH —
# evaluated once at the end (run_bin_packing_held_out), not every search
# iteration, using a disjoint seed range (6-10) so it's a genuine held-out
# split, not a re-score of the training instances.
HELD_OUT_SEEDS = (6, 7, 8, 9, 10)
HELD_OUT_CONFIGS = tuple((n, c) for n in (1000, 5000, 10000) for c in (100, 500))


def generate_instance(seed, n_items=None, capacity=None):
    """Weibull-distributed integer item sizes in [1, capacity] (Decision #6).
    n_items/capacity default to the scored set's globals; held-out reporting
    passes explicit values from HELD_OUT_CONFIGS."""
    n_items = N_ITEMS if n_items is None else n_items
    capacity = BIN_CAPACITY if capacity is None else capacity
    rng = np.random.RandomState(seed)
    raw = rng.weibull(3.0, size=n_items) * (capacity * 0.45)
    items = np.clip(np.round(raw), 1, capacity).astype(int)
    return items.tolist()


def online_pack(items, capacity, priority_fn):
    """ONLINE placement: each item is placed on arrival, no reordering. The
    heuristic scores the bins that can hold the item; it goes to the highest-
    scoring one, else a new bin opens. This loop is fixed — only the heuristic
    is the treatment, so a mutation cannot cheat by sorting items (going offline).

    Capacity-indexed (task 0096): scanning every open bin for each item is
    O(n*bins) — at n=10000 that's ~100x the work of n=1000 (both items and
    bins scale with n). `buckets[c]` holds the bins currently at exactly `c`
    remaining capacity (capacities are always integers here, so this is exact,
    not an approximation); a scan only visits buckets in [item, capacity], not
    every bin. `fit_idx.sort()` restores creation-index order before argmax so
    tie-breaking among equal-capacity bins is byte-identical to the original
    flat-list scan (verified: 0 mismatches across 600 randomized trials
    including a constant-score heuristic that forces every candidate to tie —
    the case this optimization could most plausibly have broken).
    """
    buckets = [[] for _ in range(capacity + 1)]  # buckets[c] = bin indices at remaining==c
    bin_capacity = []  # bin_capacity[i] = current remaining capacity of bin i

    for item in items:
        fit_idx = []
        for c in range(item, capacity + 1):
            fit_idx.extend(buckets[c])
        if fit_idx:
            fit_idx.sort()
            scores = priority_fn(
                item, np.array([bin_capacity[i] for i in fit_idx], dtype=float)
            )
            chosen = fit_idx[int(np.argmax(scores))]
            old_cap = bin_capacity[chosen]
            buckets[old_cap].remove(chosen)
            new_cap = old_cap - item
            bin_capacity[chosen] = new_cap
            buckets[new_cap].append(chosen)
        else:
            new_idx = len(bin_capacity)
            new_cap = capacity - item
            bin_capacity.append(new_cap)
            buckets[new_cap].append(new_idx)
    return len(bin_capacity)


# EVOLVE-BLOCK-START
# CONTROL FLOW
# F_mut: the bin-scoring heuristic — the ONLY thing evolution changes. Given the
# arriving `item` size and `bins` (a numpy array of the remaining capacities of
# the bins that can hold it), return a score per bin; the item is placed in the
# highest-scoring bin. A better heuristic packs into fewer bins.
def priority(item, bins):
    """Best-fit baseline: prefer the bin left with the least slack after placing."""
    return -(bins - item)
# EVOLVE-BLOCK-END
