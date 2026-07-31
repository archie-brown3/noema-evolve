#!/usr/bin/env python3
"""Generate seed- and condition-specific stock OpenEvolve configurations."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SEEDS = (42, 43, 44, 45, 46)


def generate(output: Path, run_root: Path) -> list[Path]:
    base_path = ROOT / "examples/bin_packing/config_openevolve_null_baseline.yaml"
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    prompt_bundle = (ROOT / "experiments/0132/prompt-normalized-templates").resolve()
    paths: list[Path] = []
    conditions = {
        "stock-native": {"parallel": 4, "prompt_normalized": False},
        "stock-sequential": {"parallel": 1, "prompt_normalized": False},
        "stock-prompt-normalized": {"parallel": 4, "prompt_normalized": True},
    }
    output.mkdir(parents=True, exist_ok=True)
    for condition, values in conditions.items():
        for seed in SEEDS:
            config = deepcopy(base)
            config["random_seed"] = seed
            config["evaluator"]["parallel_evaluations"] = values["parallel"]
            config["database"]["db_path"] = str(
                (run_root / f"{condition}-s{seed}" / "db").resolve()
            )
            if values["prompt_normalized"]:
                config["prompt"]["template_dir"] = str(prompt_bundle)
            else:
                config["prompt"].pop("template_dir", None)
            path = output / f"{condition}-s{seed}.yaml"
            path.write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
            paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("generated-configs"),
    )
    parser.add_argument(
        "--run-root", type=Path, default=Path("/private/tmp/noema-0132-runs")
    )
    args = parser.parse_args()
    paths = generate(args.output, args.run_root)
    print(f"generated {len(paths)} configs in {args.output}")


if __name__ == "__main__":
    main()
