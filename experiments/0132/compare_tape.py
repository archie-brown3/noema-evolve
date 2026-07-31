#!/usr/bin/env python3
"""Compare two tape trajectories step for step (layer 5 verdict).

Exit code 0 = identical trajectories, 1 = divergence. The first divergence is
what matters: everything after it is downstream of the same cause.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tape import Step, diff_trajectories  # noqa: E402


def load(path: Path) -> tuple[dict, list[Step]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload, [Step(**s) for s in payload["steps"]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--left", type=Path, required=True)
    ap.add_argument("--right", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    left_meta, left = load(args.left)
    right_meta, right = load(args.right)
    divergences = diff_trajectories(left, right)

    report = {
        "left": {k: v for k, v in left_meta.items() if k != "steps"},
        "right": {k: v for k, v in right_meta.items() if k != "steps"},
        "steps_left": len(left),
        "steps_right": len(right),
        "identical": not divergences,
        "first_divergence": divergences[0]["index"] if divergences else None,
        "divergence_count": len(divergences),
        "divergences": divergences[:5],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "divergences"}, indent=2))
    if divergences:
        print(f"\nfirst divergence at step {divergences[0]['index']}:")
        print(json.dumps(divergences[0], indent=2)[:1500])
    raise SystemExit(1 if divergences else 0)


if __name__ == "__main__":
    main()
