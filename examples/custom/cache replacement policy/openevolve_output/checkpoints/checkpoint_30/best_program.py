def score(age, frequency, recency, size, cache_size, now):
    return -(recency / (age + 1) + (1 / (frequency + 1)) * (age * 0.8 + recency * 0.2))