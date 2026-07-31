#!/usr/bin/env python3
"""Render a fixed mutation prompt in both environments and compare it exactly."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def render(template_dir: str | None) -> dict[str, str]:
    from openevolve.config import PromptConfig
    from openevolve.prompt.sampler import PromptSampler

    base = yaml.safe_load(
        (ROOT / "examples/bin_packing/config_openevolve_null_baseline.yaml").read_text(
            encoding="utf-8"
        )
    )
    code = (ROOT / "examples/bin_packing/initial_program.py").read_text(
        encoding="utf-8"
    )
    metrics = {
        "combined_score": 0.9562,
        "bins_used": 2100.0,
        "lower_bound": 2080.0,
    }
    program = {"id": "fixture-parent", "code": code, "metrics": metrics}
    sampler = PromptSampler(
        PromptConfig(
            template_dir=template_dir,
            system_message=base["prompt"]["system_message"],
            use_template_stochasticity=False,
            include_artifacts=False,
            num_top_programs=1,
            num_diverse_programs=0,
        )
    )
    return sampler.build_prompt(
        current_program=code,
        parent_program=code,
        program_metrics=metrics,
        previous_programs=[program],
        top_programs=[program],
        inspirations=[],
        language="python",
        evolution_round=0,
        diff_based_evolution=False,
        feature_dimensions=[],
    )


def run_renderer(python: Path, template_dir: Path | None) -> dict[str, str]:
    command = [str(python), str(Path(__file__).resolve()), "--render"]
    if template_dir is not None:
        command.extend(["--template-dir", str(template_dir)])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def digest(prompt: dict[str, str]) -> str:
    payload = json.dumps(prompt, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def check() -> None:
    noema_python = ROOT / ".venv/bin/python"
    stock_python = ROOT / ".openevolve-stock/.venv/bin/python"
    prompt_bundle = ROOT / "experiments/0132/prompt-normalized-templates"
    noema = run_renderer(noema_python, None)
    stock = run_renderer(stock_python, prompt_bundle)
    if noema != stock:
        difference = difflib.unified_diff(
            json.dumps(noema, indent=2).splitlines(),
            json.dumps(stock, indent=2).splitlines(),
            fromfile="noema",
            tofile="stock-prompt-normalized",
            lineterm="",
        )
        raise SystemExit("prompt parity failed:\n" + "\n".join(difference))
    print(f"prompt parity passed: sha256={digest(noema)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--template-dir")
    args = parser.parse_args()
    if args.render:
        print(json.dumps(render(args.template_dir), sort_keys=True))
    else:
        check()


if __name__ == "__main__":
    main()
