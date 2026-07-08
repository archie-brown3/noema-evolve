def score(age, frequency, recency, size, cache_size, now):
    return -((recency / (age + 1)) + (age / (frequency + 1)) * (recency / (age + 1)))