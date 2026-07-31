# EVOLVE-BLOCK-START
"""Function minimization example for OpenEvolve"""
import numpy as np


def search_algorithm(iterations=1000, bounds=(-5, 5)):
    lo, hi = bounds
    span = hi - lo

    phi1 = 0.618033988749895
    phi2 = 0.3819660112501051

    best_x = np.random.uniform(lo, hi)
    best_y = np.random.uniform(lo, hi)
    best_val = evaluate_function(best_x, best_y)

    n_restarts = max(4, (5 * iterations // 6) // 90)
    budget = (5 * iterations // 6) // n_restarts

    for ri in range(n_restarts):
        k = ri % 5
        if k == 0:
            x, y = best_x, best_y
        elif k == 1:
            x = lo + span * ((ri * phi1) % 1.0)
            y = lo + span * ((ri * phi2) % 1.0)
        elif k == 2:
            x = lo + span * np.random.random()
            y = lo + span * np.random.random()
        elif k == 3:
            x = lo + span * ((ri * 0.17) % 1.0)
            y = lo + span * ((ri * 0.83) % 1.0)
        else:
            x = best_x + span * 0.1 * (np.random.random() - 0.5)
            y = best_y + span * 0.1 * (np.random.random() - 0.5)
            x, y = np.clip(x, lo, hi), np.clip(y, lo, hi)

        vx = vy = 0.0
        stall = 0

        for t in range(budget):
            val = evaluate_function(x, y)
            if val < best_val:
                best_x, best_y, best_val = x, y, val
                stall = 0
            else:
                stall += 1

            frac = t / budget
            lr = 0.12 * np.exp(-3.5 * frac) + 0.001

            gx = np.cos(x) * np.cos(y) + y * np.cos(x * y) + x / 10.0
            gy = -np.sin(x) * np.sin(y) + x * np.cos(x * y) + y / 10.0

            vx = 0.88 * vx - lr * gx
            vy = 0.88 * vy - lr * gy

            x = np.clip(x + vx, lo, hi)
            y = np.clip(y + vy, lo, hi)

            if x == lo or x == hi:
                vx *= -0.5
            if y == lo or y == hi:
                vy *= -0.5

            if stall > budget // 5:
                vx += np.random.normal(0, lr * span * 0.3)
                vy += np.random.normal(0, lr * span * 0.3)
                stall = 0

    refine_iters = iterations // 6
    x, y, vx, vy = best_x, best_y, 0.0, 0.0

    for t in range(refine_iters):
        frac = t / refine_iters
        lr = 0.005 * np.exp(-3.0 * frac) + 0.0003

        gx = np.cos(x) * np.cos(y) + y * np.cos(x * y) + x / 10.0
        gy = -np.sin(x) * np.sin(y) + x * np.cos(x * y) + y / 10.0

        vx = 0.9 * vx - lr * gx
        vy = 0.9 * vy - lr * gy

        x = np.clip(x + vx, lo, hi)
        y = np.clip(y + vy, lo, hi)

        val = evaluate_function(x, y)
        if val < best_val:
            best_x, best_y, best_val = x, y, val

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