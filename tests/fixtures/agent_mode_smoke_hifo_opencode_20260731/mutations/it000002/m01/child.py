# EVOLVE-BLOCK-START
"""Function minimization example for OpenEvolve"""
import numpy as np


def search_algorithm(iterations=1000, bounds=(-5, 5)):
    lo, hi = bounds
    rng = hi - lo

    bx = np.random.uniform(lo, hi)
    by = np.random.uniform(lo, hi)
    bv = evaluate_function(bx, by)

    nr = max(3, iterations // 90)
    bgt = iterations // nr

    for ri in range(nr):
        k = ri % 3
        if k == 0:
            x, y = bx, by
        elif k == 1:
            x = lo + rng * ((ri * 0.618033988749895) % 1.0)
            y = lo + rng * ((ri * 0.3819660112501051) % 1.0)
        else:
            x = lo + rng * np.random.random()
            y = lo + rng * np.random.random()

        vx = vy = st = 0

        for t in range(bgt):
            v = evaluate_function(x, y)
            if v < bv:
                bx, by, bv = x, y, v
                st = 0
            else:
                st += 1

            frac = t / bgt
            lr = 0.15 * np.exp(-3.0 * frac) + 0.0002
            mo = 0.92 - 0.07 * frac

            gx = np.cos(x) * np.cos(y) + y * np.cos(x * y) + x / 10.0
            gy = -np.sin(x) * np.sin(y) + x * np.cos(x * y) + y / 10.0

            vx = mo * vx - lr * gx
            vy = mo * vy - lr * gy

            x += vx
            y += vy

            if x <= lo or x >= hi:
                x = np.clip(x, lo, hi)
                vx *= -0.5
            if y <= lo or y >= hi:
                y = np.clip(y, lo, hi)
                vy *= -0.5

            if st > bgt // 6:
                x = np.clip(bx + np.random.normal(0, rng * 0.04), lo, hi)
                y = np.clip(by + np.random.normal(0, rng * 0.04), lo, hi)
                vx = vy = st = 0

    return bx, by, bv


# EVOLVE-BLOCK-END


# This part remains fixed (not evolved)
def evaluate_function(x, y):
    """The complex function we're trying to minimize"""
    return np.sin(x) * np.cos(y) + np.sin(x * y) + (x**2 + y**2) / 20


def run_search():
    x, y, value = search_algorithm()
    return x, y, value


if __name__ == "__main__":
    x, y, value = run_search()
    print(f"Found minimum at ({x}, {y}) with value {value}")