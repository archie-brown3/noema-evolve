def score(age, frequency, recency, size, cache_size, now):
    return - ((age + 1.09 * recency + 0.31 * frequency) ** 1.17 / (age + 1.23) + 0.93 * (frequency ** 0.89) / (age + 1.04) ** 1.203)