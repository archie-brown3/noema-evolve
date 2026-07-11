"""Online bin packing benchmark"""
import numpy as np
import random


# ENTRY POINT
# F_imm: the I/O contract other code (the evaluator, run_packing callers) relies
# on. Its signature and return shape must not change under mutation.
def run_bin_packing(instance_seed=42):
    """Run the bin packing solver on a deterministic instance set"""
    # Generate deterministic instance
    items = generate_instance(instance_seed)
    
    # Solve using the current strategy
    bins_used, packing = pack_items(items)
    
    return items, bins_used, packing


# HELPER FUNCTIONS
# F_imm: foundational utility, not part of the packing strategy.
def generate_instance(seed=42):
    """
    Generate a deterministic set of items for bin packing.
    
    Args:
        seed: Random seed for deterministic generation
    
    Returns:
        List of item sizes (floats in (0, 1])
    """
    # Fix random seed for reproducibility
    rng = np.random.RandomState(seed)
    
    # Generate 100 items with sizes following a mixture distribution
    # 60% small items (0-0.3), 30% medium (0.3-0.7), 10% large (0.7-1.0)
    n_items = 100
    items = []
    
    for _ in range(n_items):
        p = rng.random()
        if p < 0.6:
            # Small items
            items.append(rng.uniform(0.01, 0.3))
        elif p < 0.9:
            # Medium items
            items.append(rng.uniform(0.3, 0.7))
        else:
            # Large items
            items.append(rng.uniform(0.7, 1.0))
    
    return items


def evaluate_packing(items, bins_used, packing, bin_capacity=1.0):
    """
    Validate a packing solution and compute metrics.
    
    Args:
        items: List of item sizes
        bins_used: Number of bins claimed to be used
        packing: List where packing[i] = bin index for item i
        bin_capacity: Capacity of each bin (default 1.0)
    
    Returns:
        Dictionary with validity flag and objective value
    """
    n_items = len(items)
    
    # Validate packing format
    if len(packing) != n_items:
        return {"valid": False, "objective": float("inf"), "error": f"Packing length {len(packing)} != items length {n_items}"}
    
    # Check bin indices are non-negative integers
    max_bin = max(packing) if packing else -1
    if max_bin >= bins_used:
        return {"valid": False, "objective": float("inf"), "error": f"Bin index {max_bin} >= bins_used {bins_used}"}
    
    if min(packing) < 0:
        return {"valid": False, "objective": float("inf"), "error": f"Negative bin index {min(packing)}"}
    
    # Compute bin loads
    bin_loads = [0.0] * bins_used
    for i, (size, bin_idx) in enumerate(zip(items, packing)):
        bin_loads[bin_idx] += size
    
    # Check no bin exceeds capacity
    for b in range(bins_used):
        if bin_loads[b] > bin_capacity + 1e-9:
            return {"valid": False, "objective": float("inf"), "error": f"Bin {b} overloaded: {bin_loads[b]} > {bin_capacity}"}
    
    # Compute objective: lower bound is ceil(sum(items)/capacity)
    lower_bound = np.ceil(np.sum(items) / bin_capacity)
    optimality_gap = (bins_used - lower_bound) / lower_bound if lower_bound > 0 else 0
    
    return {
        "valid": True,
        "objective": bins_used,
        "lower_bound": lower_bound,
        "optimality_gap": optimality_gap,
        "bin_loads": bin_loads
    }


# EVOLVE-BLOCK-START
# CONTROL FLOW
# F_mut: the packing strategy. Evolution is free to change how items are packed,
# as long as pack_items() keeps returning (bins_used, packing) — the I/O contract
# run_bin_packing() (F_imm, above) depends on.
def pack_items(items, bin_capacity=1.0):
    """
    Pack items into bins of capacity bin_capacity using a specific strategy.
    
    Args:
        items: List of item sizes (floats in (0, 1])
        bin_capacity: Capacity of each bin (default 1.0)
    
    Returns:
        Tuple of (bins_used, packing) where:
        - bins_used: Integer number of bins used
        - packing: List where packing[i] = bin index for item i
    """
    # This is a simple First-Fit Decreasing strategy
    # Evolution will improve this
    
    # Sort items in decreasing order (best-fit decreasing heuristic)
    sorted_items = sorted(enumerate(items), key=lambda x: x[1], reverse=True)
    
    bins = []  # List of current bin loads
    packing = [0] * len(items)
    
    for idx, size in sorted_items:
        # Find first bin that fits
        placed = False
        for b in range(len(bins)):
            if bins[b] + size <= bin_capacity:
                bins[b] += size
                packing[idx] = b
                placed = True
                break
        
        # If no bin fits, open a new bin
        if not placed:
            bins.append(size)
            packing[idx] = len(bins) - 1
    
    return len(bins), packing


# HELPER FUNCTIONS
# F_mut: used only by pack_items's strategy; evolution may replace
# the packing approach entirely.
def next_fit(items, bin_capacity=1.0):
    """
    Next-Fit packing heuristic.
    
    Args:
        items: List of item sizes
        bin_capacity: Capacity of each bin
    
    Returns:
        (bins_used, packing)
    """
    bins = [0.0]
    packing = []
    
    for size in items:
        if bins[-1] + size <= bin_capacity:
            bins[-1] += size
            packing.append(len(bins) - 1)
        else:
            bins.append(size)
            packing.append(len(bins) - 1)
    
    return len(bins), packing


def first_fit(items, bin_capacity=1.0):
    """
    First-Fit packing heuristic.
    
    Args:
        items: List of item sizes
        bin_capacity: Capacity of each bin
    
    Returns:
        (bins_used, packing)
    """
    bins = []
    packing = []
    
    for size in items:
        placed = False
        for b in range(len(bins)):
            if bins[b] + size <= bin_capacity:
                bins[b] += size
                packing.append(b)
                placed = True
                break
        
        if not placed:
            bins.append(size)
            packing.append(len(bins) - 1)
    
    return len(bins), packing


def best_fit(items, bin_capacity=1.0):
    """
    Best-Fit packing heuristic.
    
    Args:
        items: List of item sizes
        bin_capacity: Capacity of each bin
    
    Returns:
        (bins_used, packing)
    """
    bins = []
    packing = []
    
    for size in items:
        # Find bin with smallest remaining capacity that can fit this item
        best_bin = -1
        best_remaining = float('inf')
        
        for b in range(len(bins)):
            remaining = bin_capacity - (bins[b] + size)
            if remaining >= 0 and remaining < best_remaining:
                best_remaining = remaining
                best_bin = b
        
        if best_bin >= 0:
            bins[best_bin] += size
            packing.append(best_bin)
        else:
            bins.append(size)
            packing.append(len(bins) - 1)
    
    return len(bins), packing
# EVOLVE-BLOCK-END


if __name__ == "__main__":
    # Test the current implementation
    items = generate_instance(42)
    bins_used, packing = pack_items(items)
    
    result = evaluate_packing(items, bins_used, packing)
    
    print(f"Items: {len(items)}")
    print(f"Total size: {sum(items):.3f}")
    print(f"Bins used: {bins_used}")
    print(f"Lower bound: {result['lower_bound']}")
    print(f"Optimality gap: {result['optimality_gap']:.3f}")
    print(f"Valid: {result['valid']}")