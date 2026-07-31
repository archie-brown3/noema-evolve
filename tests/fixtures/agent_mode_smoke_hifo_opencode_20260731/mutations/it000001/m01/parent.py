# EVOLVE-BLOCK-START
"""Function minimization example for OpenEvolve"""
import numpy as np


def search_algorithm(iterations=1000, bounds=(-5, 5)):
    lo, hi = bounds
    best_x = np.random.uniform(lo, hi)
    best_y = np.random.uniform(lo, hi)
    best_val = evaluate_function(best_x, best_y)

    n_restarts = max(3, iterations // 120)
    budget = iterations // n_restarts
    span = hi - lo

    for ri in range(n_restarts):
        if ri % 3 == 0:
            x, y = best_x, best_y
        elif ri % 3 == 1:
            x = lo + span * ((ri * 0.37) % 1.0)
            y = lo + span * ((ri * 0.61) % 1.0)
        else:
            x = lo + span * np.random.random()
            y = lo + span * np.random.random()

        vx = vy = 0.0

        for t in range(budget):
            val = evaluate_function(x, y)
            if val < best_val:
                best_x, best_y, best_val = x, y, val

            frac = t / budget
            lr = 0.1 * np.exp(-4.0 * frac) + 0.002

            gx = np.cos(x) * np.cos(y) + y * np.cos(x * y) + x / 10.0
            gy = -np.sin(x) * np.sin(y) + x * np.cos(x * y) + y / 10.0

            vx = 0.85 * vx - lr * gx
            vy = 0.85 * vy - lr * gy

            x = np.clip(x + vx, lo, hi)
            y = np.clip(y + vy, lo, hi)

            if x == lo or x == hi:
                vx *= -0.5
            if y == lo or y == hi:
                vy *= -0.5

    return best_x, best_y, best_val


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
