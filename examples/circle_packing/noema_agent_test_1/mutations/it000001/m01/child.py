"""Constructor-based circle packing for n=26 circles"""
import numpy as np


# ENTRY POINT
# F_imm: the I/O contract other code (the evaluator, run_packing callers) relies
# on. Its signature and return shape must not change under mutation.
def run_packing():
    """Run the circle packing constructor for n=26"""
    centers, radii, sum_radii = construct_packing()
    return centers, radii, sum_radii


# HELPER FUNCTIONS
# F_imm: foundational utility, not part of the packing strategy.
def visualize(centers, radii):
    """
    Visualize the circle packing

    Args:
        centers: np.array of shape (n, 2) with (x, y) coordinates
        radii: np.array of shape (n) with radius of each circle
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    fig, ax = plt.subplots(figsize=(8, 8))

    # Draw unit square
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.grid(True)

    # Draw circles
    for i, (center, radius) in enumerate(zip(centers, radii)):
        circle = Circle(center, radius, alpha=0.5)
        ax.add_patch(circle)
        ax.text(center[0], center[1], str(i), ha="center", va="center")

    plt.title(f"Circle Packing (n={len(centers)}, sum={sum(radii):.6f})")
    plt.show()


# EVOLVE-BLOCK-START
# CONTROL FLOW
# F_mut: the packing strategy. Evolution is free to change how circles are
# placed, as long as construct_packing() keeps returning (centers, radii,
# sum_of_radii) — the I/O contract run_packing() (F_imm, above) depends on.
def construct_packing():
    """
    Greedy placement with aggressive local search.
    """
    n = 26
    np.random.seed(42)
    centers = []
    radii = []

    for i in range(n):
        best_pos = None
        best_r = 0.0

        for _ in range(500):
            x = np.random.uniform(0.02, 0.98)
            y = np.random.uniform(0.02, 0.98)
            r = min(x, y, 1 - x, 1 - y)

            for j, (c, rad) in enumerate(zip(centers, radii)):
                d = np.sqrt((x - c[0])**2 + (y - c[1])**2)
                r = min(r, d - rad)

            if r > best_r:
                best_r = r
                best_pos = (x, y)

        if best_pos and best_r > 0.01:
            centers.append(np.array(best_pos))
            radii.append(best_r)
        else:
            centers.append(np.array([0.5, 0.5]))
            radii.append(0.01)

    centers = np.array(centers)
    radii = np.array(radii)

    for _ in range(100):
        for i in range(n):
            for _ in range(50):
                old_pos = centers[i].copy()
                dx = np.random.uniform(-0.02, 0.02)
                dy = np.random.uniform(-0.02, 0.02)
                centers[i] += np.array([dx, dy])
                centers[i] = np.clip(centers[i], 0.01, 0.99)
                new_radii = compute_max_radii(centers)
                if np.sum(new_radii) > np.sum(radii):
                    radii = new_radii
                else:
                    centers[i] = old_pos

    sum_radii = np.sum(radii)
    return centers, radii, sum_radii


# HELPER FUNCTIONS
# F_mut: used only by construct_packing's strategy; evolution may replace
# the radius-fitting approach entirely.
def compute_max_radii(centers):
    """
    Compute the maximum possible radii for each circle position
    such that they don't overlap and stay within the unit square.

    Args:
        centers: np.array of shape (n, 2) with (x, y) coordinates

    Returns:
        np.array of shape (n) with radius of each circle
    """
    n = centers.shape[0]
    radii = np.ones(n)

    # First, limit by distance to square borders
    for i in range(n):
        x, y = centers[i]
        # Distance to borders
        radii[i] = min(x, y, 1 - x, 1 - y)

    # Then, limit by distance to other circles
    # Each pair of circles with centers at distance d can have
    # sum of radii at most d to avoid overlap
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2))

            # If current radii would cause overlap
            if radii[i] + radii[j] > dist:
                # Scale both radii proportionally
                scale = dist / (radii[i] + radii[j])
                radii[i] *= scale
                radii[j] *= scale

    return radii
# EVOLVE-BLOCK-END


if __name__ == "__main__":
    centers, radii, sum_radii = run_packing()
    print(f"Sum of radii: {sum_radii}")
    # AlphaEvolve improved this to 2.635

    # Uncomment to visualize:
    visualize(centers, radii)
