# Current Program Information
- Fitness: 1.0658
- Feature coordinates: 
- Focus areas: - Fitness unchanged at 1.0658
- No feature coordinates
- Consider simplifying - code length exceeds 500 characters



# Program Evolution History
## Previous Attempts

### Attempt 1
- Changes: Unknown changes
- Metrics: value_score: 0.9767, distance_score: 0.6661, reliability_score: 1.0000, combined_score: 1.0658
- Outcome: Improvement in all metrics

## Top Performing Programs

### Program 1 (Score: 1.0658)
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
- Design adaptive hybrid meta-heuristics synergistically fusing multiple search paradigms and dynamically tune operator parameters based on search stage or problem features
- Employ machine learning or pattern recognition to mine deep problem structures and optimal solution patterns then use learned insights to intelligently bias towards promising search regions or constructive choices
- Explore objective function engineering by introducing auxiliary or surrogate objectives or by dynamically adjusting weights to reshape the search landscape aiding escape from local optima or guiding diverse exploration
For this task, please pay special attention to: optimizing objective function evaluation criteria
Strike a balance between novel ideas and proven effective techniques.