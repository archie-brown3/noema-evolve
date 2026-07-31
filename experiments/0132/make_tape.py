#!/usr/bin/env python3
"""Generate a deterministic response tape for the circle_packing layer-5 test.

Each entry is a complete program in full-rewrite form, so the child is
parent-independent and a trajectory divergence is attributable to
selection/admission rather than to how a diff landed on a different parent.

**The immutable region is spliced, not regenerated.** `circle_packing`'s
`initial_program.py` carries EVOLVE-BLOCK markers (lines 1 and 88) and noema
enforces that boundary while stock does not. A tape of free-form programs is
therefore rejected by noema at every step and admitted by stock at every step —
a difference in what the TAPE respects, not in how the two systems behave, and
it would masquerade as total divergence. So every entry reuses the initial
program's immutable tail verbatim and varies only inside the block. This is the
same failure mode as the PE arm's interface defect (task 0122).

Entries vary a scale constant, which moves `sum_radii` and so `combined_score`
monotonically, alternating around the initial packing so the tape drives both
admissions and rejections.
"""

from __future__ import annotations

import argparse
from pathlib import Path

SCALES = [1.00, 0.85, 1.02, 0.70, 1.04, 0.60, 1.06, 0.55, 1.08, 0.50]

START = "# EVOLVE-BLOCK-START"
END = "# EVOLVE-BLOCK-END"

BLOCK = '''{start}
"""Constructor-based circle packing for n=26 circles (tape entry {index})"""
import numpy as np


def construct_packing():
    """Grid-and-shrink construction; scale {scale} distinguishes tape entries."""
    n = 26
    centers = np.zeros((n, 2))
    k = 0
    for row in range(5):
        for col in range(6):
            if k >= n:
                break
            centers[k] = [(col + 0.5) / 6.0, (row + 0.5) / 5.0]
            k += 1
    for extra in range(k, n):
        centers[extra] = [(extra - k + 0.5) / max(n - k, 1), 0.94]

    radii = np.full(n, 0.5 / 6.0) * {scale}
    for i in range(n):
        x, y = centers[i]
        radii[i] = min(radii[i], x, y, 1.0 - x, 1.0 - y)
    for i in range(n):
        for j in range(i + 1, n):
            d = np.linalg.norm(centers[i] - centers[j])
            overlap = radii[i] + radii[j] - d
            if overlap > 0:
                share = overlap / 2.0
                radii[i] = max(radii[i] - share, 0.0)
                radii[j] = max(radii[j] - share, 0.0)
    return centers, radii, float(np.sum(radii))
{end}'''


def immutable_tail(initial_program: Path) -> str:
    """Everything after EVOLVE-BLOCK-END, verbatim."""
    text = initial_program.read_text(encoding="utf-8")
    if END not in text:
        raise ValueError(f"{initial_program} has no {END} marker")
    return text.split(END, 1)[1]


def build(length: int, tail: str) -> list[str]:
    entries = []
    for i in range(length):
        block = BLOCK.format(
            start=START, end=END, index=i, scale=SCALES[i % len(SCALES)]
        )
        entries.append("```python\n" + block + tail + "\n```")
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--length", type=int, default=20)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--initial-program",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / ".openevolve-upstream/examples/circle_packing/initial_program.py",
    )
    args = ap.parse_args()
    from tape import Tape

    tape = Tape(responses=build(args.length, immutable_tail(args.initial_program)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tape.to_json(args.out)
    print(f"wrote {len(tape)} responses -> {args.out}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
