# Online bin packing (headline benchmark #2)

Serves **C3** (program quality vs published baselines). This is the *online*
FunSearch/EoH formulation — the offline version it replaced scored 1.0 on the
trivial program and had no evolutionary headroom (task 0036 finding, Decision
#48). Redesigned by task 0091.

## The problem

Integer-sized items arrive **one at a time** into bins of capacity `C = 100` and
are placed **immediately** — no reordering, no lookahead. The only evolvable code
(`F_mut`, inside the `EVOLVE-BLOCK` in `initial_program.py`) is the heuristic:

```python
def priority(item, bins):
    """Score each bin that can hold `item`; the item goes to the highest score."""
    return -(bins - item)   # best-fit baseline
```

The instance generator, the online arrival loop, and the I/O contract are `F_imm`
and are enforced immutable — a mutation cannot go offline (sort items) or change
the instances.

## Instances

Weibull-distributed integer item sizes (Decision #6), `shape = 3.0`, scaled and
clipped to `[1, C]`. The scored set is 5 seeded instances of `n = 1000` items
(`INSTANCE_SEEDS = (1, 2, 3, 4, 5)`), committed in code — no runtime downloads,
fully deterministic. Larger `n` (5k/10k) and a held-out reporting split (e.g.
seeds 6–10) can be added for the final numbers without changing the harness.

## Scoring

For each instance, `lower_bound = ceil(sum(items) / C)` (the material bound).

```
excess_i       = (bins_used_i - lower_bound_i) / lower_bound_i
combined_score = 1 / (1 + mean_i(excess_i))      # in (0, 1], higher is better
```

**Baseline:** the best-fit initial heuristic scores **≈ 0.956** (mean excess
≈ 4.6%), in line with FunSearch's published best-fit gap on Weibull instances.
That leaves real headroom: a better heuristic lowers the excess and raises the
score. `combined_score` is what noema maximizes, so it is comparable across arms;
the raw `mean_excess` (also returned) is what compares to the literature.

## Running

```
python run_noema_arm.py --arm null   --api-base http://localhost:8090/v1 --output-dir out_null
python run_noema_arm.py --arm bandit  --api-base http://localhost:8090/v1 --output-dir out_bandit --operator-menu
```

The evaluator runs each candidate in a subprocess with a wall-clock timeout and a
memory rlimit (the Evaluator is not a sandbox). Acceptance tests:
`tests/test_bin_packing_example.py`.
