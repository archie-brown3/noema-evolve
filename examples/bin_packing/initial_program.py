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


# HELPER FUNCTIONS
# F_imm: the fixed benchmark definition — the Weibull item distribution and the
# ONLINE arrival loop. Not evolvable: the LLM changes only how bins are scored,
# never the item distribution, the instance set, or the no-reordering rule.
BIN_CAPACITY = 100
N_ITEMS = 1000
# A committed, seeded instance set (no runtime downloads). The held-out split for
# reporting is documented in the README; these five are the scored set.
INSTANCE_SEEDS = (1, 2, 3, 4, 5)


def generate_instance(seed):
    """Weibull-distributed integer item sizes in [1, capacity] (Decision #6)."""
    rng = np.random.RandomState(seed)
    raw = rng.weibull(3.0, size=N_ITEMS) * 45.0
    items = np.clip(np.round(raw), 1, BIN_CAPACITY).astype(int)
    return items.tolist()


def online_pack(items, capacity, priority_fn):
    """ONLINE placement: each item is placed on arrival, no reordering. The
    heuristic scores the bins that can hold the item; it goes to the highest-
    scoring one, else a new bin opens. This loop is fixed — only the heuristic
    is the treatment, so a mutation cannot cheat by sorting items (going offline).
    """
    remaining = []  # remaining capacity of each open bin
    for item in items:
        fit_idx = [i for i, r in enumerate(remaining) if r >= item]
        if fit_idx:
            scores = priority_fn(
                item, np.array([remaining[i] for i in fit_idx], dtype=float)
            )
            chosen = fit_idx[int(np.argmax(scores))]
            remaining[chosen] -= item
        else:
            remaining.append(capacity - item)
    return len(remaining)


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
