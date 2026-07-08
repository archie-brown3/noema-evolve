"""
Seed program for the cache-replacement-policy evolution experiment.

OpenEvolve mutates only the score() function inside the EVOLVE-BLOCK.
benchmark.py provides the simulation harness — this file must define
ONLY score() (no simulate(), no imports, no other functions).

score() return value: higher = more valuable = keep in cache.
The cache evicts the resident with the LOWEST score.
Seed -recency reproduces LRU exactly.
"""

# EVOLVE-BLOCK-START
def score(age, frequency, recency, size, cache_size, now):
    """
    age        = now - insert_time       (how long in cache)
    frequency  = total accesses while resident
    recency    = now - last_access_time  (0 = just accessed)
    size       = item size (1 in uniform experiments)
    cache_size = capacity (constant within a run)
    now        = current logical timestamp
    Higher score = keep; lowest score is evicted.
    """
    return - (age + (frequency / (frequency + 1)) * recency) / (age + frequency + 1)
# EVOLVE-BLOCK-END