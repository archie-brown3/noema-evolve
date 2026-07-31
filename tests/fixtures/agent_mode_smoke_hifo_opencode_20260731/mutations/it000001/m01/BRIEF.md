# Current Program Information
- Fitness: 1.4995
- Feature coordinates: 
- Focus areas: - Fitness improved: 1.0658 → 1.4995
- No feature coordinates
- Consider simplifying - code length exceeds 500 characters



# Program Evolution History
## Previous Attempts

### Attempt 2
- Changes: Unknown changes
- Metrics: value_score: 0.9767, distance_score: 0.6661, reliability_score: 1.0000, combined_score: 1.0658
- Outcome: Improvement in all metrics

### Attempt 1
- Changes: Unknown changes
- Metrics: value_score: 0.9997, distance_score: 0.9995, reliability_score: 1.0000, combined_score: 1.4995
- Outcome: Mixed results

## Top Performing Programs

### Program 1 (Score: 1.4995)
```python
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

```
Key features: Performs well on value_score (0.9997), Performs well on distance_score (0.9995), Performs well on reliability_score (1.0000), Performs well on combined_score (1.4995)

### Program 2 (Score: 1.0658)
```python
# EVOLVE-BLOCK-START
"""Function minimization example for OpenEvolve"""
import numpy as np


def search_algorithm(iterations=1000, bounds=(-5, 5)):
    """
    A simple random search algorithm that often gets stuck in local minima.

    Args:
        iterations: Number of iterations to run
        bounds: Bounds for the search space (min, max)

    Returns:
        Tuple of (best_x, best_y, best_value)
    """
    # Initialize with a random point
    best_x = np.random.uniform(bounds[0], bounds[1])
    best_y = np.random.uniform(bounds[0], bounds[1])
    best_value = evaluate_function(best_x, best_y)

    for _ in range(iterations):
        # Simple random search
        x = np.random.uniform(bounds[0], bounds[1])
        y = np.random.uniform(bounds[0], bounds[1])
        value = evaluate_function(x, y)

        if value < best_value:
            best_value = value
            best_x, best_y = x, y

    return best_x, best_y, best_value


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

```
Key features: Performs well on value_score (0.9767), Performs well on distance_score (0.6661), Performs well on reliability_score (1.0000), Performs well on combined_score (1.0658)



# Current Program
```python
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

```

# Task
Rewrite the program to improve its FITNESS SCORE.
The system maintains diversity across these dimensions: complexity, diversity
Different solutions with similar fitness but different features are valuable.
Provide the complete new program code.

IMPORTANT: Make sure your rewritten program maintains the same inputs and outputs
as the original program, but with improved internal implementation.

```python
# Your rewritten program here
```

# Coordination Guidance
Consider these successful design principles I've observed recently:
- Construct problem specialized efficient solution representations and co design dedicated core operators to fully leverage representation structure for powerful solution space exploration
- Implement intelligent diversification and restart strategies based on solution feature space analysis systematically targeting uncovered feature regions to promote global search coverage and escape deep local optima
- Prefer gradient-aware local steps with occasional global restarts when progress stalls.
For this task, please pay special attention to: refining core evaluation and scoring functions
Strike a balance between novel ideas and proven effective techniques.