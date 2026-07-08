def score(age, frequency, recency, size, cache_size, now):
    return - ((age + 1.3 * recency + 0.4 * frequency) ** 1.2 / (age + 1.2) + (frequency ** 0.85) / (age + 1) ** 1.05)