def score(age, frequency, recency, size, cache_size, now):
    return - ((age + 1.1 * recency + 0.2 * frequency) ** 1.2 / (age + 1) + (frequency ** 0.7) / (age + 1) ** 1.1)