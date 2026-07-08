def score(age, frequency, recency, size, cache_size, now):
    return - ((age + 1.2 * recency + 0.3 * frequency) ** 1.2 / (age + 1.1) + (frequency ** 0.8) / (age + 1) ** 1.1)