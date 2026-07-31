"""Constructor-based circle packing for n=26 circles."""
import numpy as np


# ENTRY POINT
def run_packing():
    """Run the circle packing constructor for n=26."""
    centers, radii, sum_radii = construct_packing()
    return centers, radii, sum_radii


def visualize(centers, radii):
    """Visualize the circle packing."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.grid(True)

    for i, (center, radius) in enumerate(zip(centers, radii)):
        circle = Circle(center, radius, alpha=0.5)
        ax.add_patch(circle)
        ax.text(center[0], center[1], str(i), ha="center", va="center")

    plt.title(f"Circle Packing (n={len(centers)}, sum={sum(radii):.6f})")
    plt.show()


# EVOLVE-BLOCK-START
def construct_packing():
    """
    Return a fixed, valid arrangement of 26 circles in the unit square.

    Returns:
        centers: np.ndarray with shape (26, 2)
        radii: np.ndarray with shape (26,)
        sum_of_radii: float
    """
    centers = np.array(
        [
            [0.06956689, 0.06956689],
            [0.50207311, 0.08575820],
            [0.93189267, 0.06810733],
            [0.06511170, 0.20417177],
            [0.27451794, 0.15095161],
            [0.50225631, 0.28464610],
            [0.72935053, 0.15058334],
            [0.93719443, 0.19891283],
            [0.12832468, 0.37043739],
            [0.32781443, 0.38297941],
            [0.67736080, 0.38326551],
            [0.88240894, 0.37078922],
            [0.07127681, 0.54541436],
            [0.24292127, 0.55022016],
            [0.50194037, 0.55643443],
            [0.76104334, 0.55197022],
            [0.93076227, 0.55125238],
            [0.11749718, 0.72844249],
            [0.32014330, 0.71920611],
            [0.68255377, 0.72015240],
            [0.88369464, 0.73072628],
            [0.07894069, 0.92105931],
            [0.25911172, 0.89719624],
            [0.50114314, 0.85754605],
            [0.74258620, 0.89769545],
            [0.92164792, 0.92164792],
        ],
        dtype=float,
    )

    radii = np.array(
        [
            0.06956689,
            0.08575820,
            0.06810733,
            0.06511170,
            0.15095161,
            0.11312978,
            0.15058334,
            0.06280557,
            0.11276504,
            0.08711858,
            0.08783630,
            0.11759106,
            0.07127681,
            0.10043492,
            0.15865872,
            0.10048270,
            0.06923773,
            0.11749718,
            0.08535931,
            0.08511324,
            0.11630536,
            0.07894069,
            0.10280376,
            0.14245395,
            0.10230455,
            0.07835208,
        ],
        dtype=float,
    )

    # Protect against decimal rounding causing microscopic violations.
    radii = np.maximum(0.0, radii - 1e-7)

    return centers, radii, float(np.sum(radii))


def compute_max_radii(centers):
    """
    Compute valid radii for fixed centres using iterative pairwise reduction.

    Each returned radius respects both the square boundary and every pairwise
    non-overlap constraint.
    """
    centers = np.asarray(centers, dtype=float)
    n = centers.shape[0]

    radii = np.minimum.reduce(
        (
            centers[:, 0],
            centers[:, 1],
            1.0 - centers[:, 0],
            1.0 - centers[:, 1],
        )
    )

    for _ in range(n):
        changed = False

        for i in range(n):
            for j in range(i + 1, n):
                distance = float(np.linalg.norm(centers[i] - centers[j]))
                total = radii[i] + radii[j]

                if total > distance:
                    if total <= 0.0:
                        radii[i] = 0.0
                        radii[j] = 0.0
                    else:
                        scale = distance / total
                        radii[i] *= scale
                        radii[j] *= scale

                    changed = True

        if not changed:
            break

    return np.maximum(radii, 0.0)


# EVOLVE-BLOCK-END

if __name__ == "__main__":
    centers, radii, sum_radii = run_packing()
    print(f"Sum of radii: {sum_radii}")
    visualize(centers, radii)