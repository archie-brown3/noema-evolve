#!/usr/bin/env python3
"""Build and validate the task-0132 pre-run manifest. No network or API calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

import yaml
from prepare_configs import generate

ROOT = Path(__file__).resolve().parents[2]
PINNED_STOCK = "411fb59c886c18704caaffb611e17cf9e7d824d2"
SEEDS = [42, 43, 44, 45, 46]
COMPLETED_MUTATIONS = 200
ENDPOINT_MARGIN_MEAN_EXCESS_BINS = 0.2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def git_state(cwd: Path) -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.rstrip("\n")
    patch = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=cwd,
        check=True,
        capture_output=True,
    ).stdout
    untracked = git(cwd, "ls-files", "--others", "--exclude-standard").splitlines()
    digest = hashlib.sha256(patch)
    for name in sorted(untracked):
        path = cwd / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return {
        "revision": git(cwd, "rev-parse", "HEAD"),
        "clean": not bool(status),
        "status": status.splitlines(),
        "dirty_sha256": digest.hexdigest() if status else None,
    }


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def benchmark_freeze() -> dict:
    program_path = ROOT / "examples/bin_packing/initial_program.py"
    benchmark = load_module(program_path, "task_0132_initial_program")
    instances = {
        str(seed): benchmark.generate_instance(seed)
        for seed in benchmark.INSTANCE_SEEDS
    }
    results = [list(item) for item in benchmark.run_bin_packing(42)]
    excess_bins = [bins - lower_bound for bins, lower_bound in results]
    relative_excess = [
        (bins - lower_bound) / lower_bound for bins, lower_bound in results
    ]
    mean_excess = sum(relative_excess) / len(relative_excess)
    combined_score = 1.0 / (1.0 + mean_excess)
    one_bin_score_steps = []
    for index, (bins, lower_bound) in enumerate(results):
        changed = list(relative_excess)
        changed[index] = (bins + 1 - lower_bound) / lower_bound
        changed_score = 1.0 / (1.0 + sum(changed) / len(changed))
        one_bin_score_steps.append(combined_score - changed_score)
    return {
        "definition": {
            "mode": "online_arrival_order",
            "capacity": benchmark.BIN_CAPACITY,
            "items_per_instance": benchmark.N_ITEMS,
            "instance_seeds": list(benchmark.INSTANCE_SEEDS),
            "distribution": "round(Weibull(shape=3.0) * 45.0), clipped to [1, 100]",
            "evolvable_region": "single EVOLVE-BLOCK containing priority(item, bins)",
            "score": "1 / (1 + mean((bins_used-lower_bound)/lower_bound))",
            "raw_endpoint": "mean(bins_used-lower_bound) across five instances",
        },
        "instance_sha256": {
            seed: canonical_sha256(items) for seed, items in instances.items()
        },
        "all_instances_sha256": canonical_sha256(instances),
        "initial_program": {
            "per_instance_bins_and_lower_bounds": results,
            "per_instance_excess_bins": excess_bins,
            "total_bins": sum(item[0] for item in results),
            "total_lower_bound": sum(item[1] for item in results),
            "mean_excess_bins": sum(excess_bins) / len(excess_bins),
            "mean_relative_excess": mean_excess,
            "combined_score": combined_score,
        },
        "one_aggregate_bin_translation": {
            "mean_excess_bins": 1.0 / len(results),
            "score_step_min": min(one_bin_score_steps),
            "score_step_max": max(one_bin_score_steps),
            "note": "score translation is baseline-local; raw bins remain primary",
        },
    }


def environment_freeze(python: Path) -> dict:
    version = subprocess.run(
        [str(python), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    freeze_payload = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import importlib.metadata as m,json;"
                "print(json.dumps(sorted("
                "f'{d.metadata[\"Name\"]}=={d.version}' for d in m.distributions() "
                "if d.metadata.get(\"Name\"))))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    freeze = json.loads(freeze_payload)
    return {
        "python": (version.stdout or version.stderr).strip(),
        "dependency_freeze": freeze,
        "dependency_freeze_sha256": canonical_sha256(freeze),
    }


def validate_stock_config(
    path: Path, *, seed: int, parallel: int, prompt_normalized: bool
) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected = {
        "max_iterations": COMPLETED_MUTATIONS,
        "prompt.num_top_programs": 1,
        "prompt.num_diverse_programs": 0,
        "prompt.include_artifacts": False,
        "prompt.use_template_stochasticity": False,
        "database.population_size": 60,
        "database.archive_size": 25,
        "database.num_islands": 4,
        "database.elite_selection_ratio": 0.3,
        "database.exploitation_ratio": 0.7,
        "evaluator.cascade_evaluation": False,
        "evaluator.parallel_evaluations": parallel,
        "random_seed": seed,
        "prompt.template_dir": (
            str((ROOT / "experiments/0132/prompt-normalized-templates").resolve())
            if prompt_normalized
            else None
        ),
    }
    actual = {
        "max_iterations": config["max_iterations"],
        "prompt.num_top_programs": config["prompt"]["num_top_programs"],
        "prompt.num_diverse_programs": config["prompt"]["num_diverse_programs"],
        "prompt.include_artifacts": config["prompt"]["include_artifacts"],
        "prompt.use_template_stochasticity": config["prompt"][
            "use_template_stochasticity"
        ],
        "database.population_size": config["database"]["population_size"],
        "database.archive_size": config["database"]["archive_size"],
        "database.num_islands": config["database"]["num_islands"],
        "database.elite_selection_ratio": config["database"][
            "elite_selection_ratio"
        ],
        "database.exploitation_ratio": config["database"]["exploitation_ratio"],
        "evaluator.cascade_evaluation": config["evaluator"]["cascade_evaluation"],
        "evaluator.parallel_evaluations": config["evaluator"][
            "parallel_evaluations"
        ],
        "random_seed": config["random_seed"],
        "prompt.template_dir": config["prompt"].get("template_dir"),
    }
    if actual != expected:
        raise SystemExit(f"stock config mismatch:\nexpected={expected}\nactual={actual}")
    return actual


def launch_commands(config_dir: Path, run_root: Path, noema_source: Path) -> dict:
    stock_native = []
    stock_sequential = []
    stock_prompt_normalized = []
    noema = []
    for seed in SEEDS:
        for condition, destination in (
            ("stock-native", stock_native),
            ("stock-sequential", stock_sequential),
            ("stock-prompt-normalized", stock_prompt_normalized),
        ):
            env = [
                f"OPENEVOLVE_USAGE_LOG={run_root}/{condition}-s{seed}/llm_calls.jsonl",
                f"OPENEVOLVE_ATTEMPT_LOG={run_root}/{condition}-s{seed}/attempt_trace.jsonl",
            ]
            if condition == "stock-prompt-normalized":
                env.append(
                    "OPENEVOLVE_PROMPT_METRIC_FIELDS=combined_score,bins_used,lower_bound"
                )
            destination.append(
                " ".join(
                    [
                        *env,
                        ".openevolve-stock/.venv/bin/openevolve-run",
                        f"{noema_source}/examples/bin_packing/initial_program.py",
                        f"{noema_source}/examples/bin_packing/evaluator.py",
                        f"--config {config_dir}/{condition}-s{seed}.yaml",
                        f"--output {run_root}/{condition}-s{seed}",
                        f"--iterations {COMPLETED_MUTATIONS}",
                        "--api-base https://openrouter.ai/api/v1",
                        "--primary-model deepseek/deepseek-v4-flash",
                    ]
                )
            )
        noema.append(
            " ".join(
                [
                    f"PYTHONPATH={noema_source}",
                    f"{ROOT}/.venv/bin/python",
                    f"{noema_source}/examples/bin_packing/run_noema_arm.py",
                    "--arm null --substrate islands --selection stock_openevolve",
                    "--api-base https://openrouter.ai/api/v1",
                    "--api-key \"$OPENROUTER_API_KEY\"",
                    "--model deepseek/deepseek-v4-flash",
                    f"--seed {seed}",
                    f"--completed-mutations {COMPLETED_MUTATIONS}",
                    "--num-inspirations 0 --num-top-programs 1",
                    "--num-previous-programs 1",
                    "--budget-tokens 2000000",
                    f"--output-dir {run_root}/noema-null-s{seed}",
                ]
            )
        )
    return {
        "stock_native": stock_native,
        "noema_null": noema,
        "stock_prompt_normalized": stock_prompt_normalized,
        "stock_sequential_diagnostic": stock_sequential,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "experiments/0132/preflight-manifest.json",
    )
    parser.add_argument(
        "--pristine-stock", type=Path, default=Path("/private/tmp/openevolve-stock")
    )
    parser.add_argument(
        "--experimental-stock", type=Path, default=ROOT / ".openevolve-stock"
    )
    parser.add_argument(
        "--run-root", type=Path, default=Path("/private/tmp/noema-0132-runs")
    )
    parser.add_argument(
        "--noema-source", type=Path, default=Path("/private/tmp/noema-0132-source")
    )
    args = parser.parse_args()

    pristine = git_state(args.pristine_stock)
    experimental = git_state(args.experimental_stock)
    if pristine["revision"] != PINNED_STOCK or not pristine["clean"]:
        raise SystemExit("pristine stock checkout is not clean at the pinned commit")
    if experimental["revision"] != PINNED_STOCK:
        raise SystemExit("experimental stock worktree is not based on the pinned commit")
    noema_source = git_state(args.noema_source)
    expected_noema_changes = {
        "examples/bin_packing/evaluator.py",
        "examples/bin_packing/run_noema_arm.py",
        "noema/controller.py",
        "noema/trace.py",
    }
    actual_noema_changes = {
        line[3:] for line in noema_source["status"] if len(line) > 3
    }
    if actual_noema_changes != expected_noema_changes:
        raise SystemExit(
            "isolated noema source contains unexpected changes: "
            f"{sorted(actual_noema_changes ^ expected_noema_changes)}"
        )

    files = [
        ROOT / "examples/bin_packing/initial_program.py",
        ROOT / "examples/bin_packing/evaluator.py",
        ROOT / "examples/bin_packing/config_openevolve_null_baseline.yaml",
        ROOT / "examples/bin_packing/run_noema_arm.py",
        ROOT / "noema/controller.py",
        ROOT / "noema/trace.py",
        ROOT / "experiments/0132/preflight.py",
        ROOT / "experiments/0132/normalize.py",
        ROOT / "experiments/0132/prepare_configs.py",
        ROOT / "experiments/0132/check_prompt_parity.py",
        ROOT / "experiments/0132/run_stage1_replay.py",
        ROOT / "experiments/0132/replay_noema.py",
        ROOT / "experiments/0132/replay_stock.py",
        ROOT / "experiments/0132/replay-fixtures/initial_program.py",
        ROOT / "experiments/0132/replay-fixtures/evaluator.py",
        ROOT / "experiments/0132/replay-fixtures/scenarios.json",
        ROOT / "experiments/0132/stage1-replay-report.json",
        ROOT / "experiments/0132/prompt-normalized-templates/manifest.json",
    ]
    config_dir = ROOT / "experiments/0132/generated-configs"
    config_paths = generate(config_dir, args.run_root)
    config_values = {}
    for path in config_paths:
        condition, seed_text = path.stem.rsplit("-s", 1)
        seed = int(seed_text)
        config_values[path.name] = validate_stock_config(
            path,
            seed=seed,
            parallel=1 if condition == "stock-sequential" else 4,
            prompt_normalized=condition == "stock-prompt-normalized",
        )
    manifest = {
        "schema_version": 1,
        "task": "0132",
        "approved_to_launch": False,
        "approval_required": [
            "Archie approval of the proposed practical equivalence margin",
            "condition matrix and five-seed API spend",
            "exact source snapshots and patch hashes",
        ],
        "stage_0": {
            "status": "frozen_pending_margin_approval",
            "primary_estimand": (
                "paired per-seed difference (Noema minus Stock) in final "
                "mean_excess_bins at exactly 200 terminal mutation requests"
            ),
            "trajectory_estimands": [
                "paired best-so-far mean_excess_bins difference at requests 50, 100, 150, 200",
                "paired area under the best-so-far mean_excess_bins trajectory through request 200",
            ],
            "proposed_endpoint_margin": {
                "absolute_mean_excess_bins": ENDPOINT_MARGIN_MEAN_EXCESS_BINS,
                "interpretation": "one aggregate bin across five fixed instances",
                "status": "awaiting_archie_approval",
            },
            "verdict_rule": {
                "pass": (
                    "all five paired endpoints are within the approved margin, "
                    "request reconciliation is exact, and no unexplained "
                    "structural discrepancy affects the endpoint"
                ),
                "fail": (
                    "any paired endpoint exceeds the margin or a structural "
                    "discrepancy invalidates the behavioral-faithfulness claim"
                ),
                "inconclusive": (
                    "a run is incomplete/unreconciled, a frozen input changes, "
                    "or five seeds do not resolve the practical claim"
                ),
                "aggregation": (
                    "report all paired seed differences and their mean/median; "
                    "do not substitute a significance test for the practical bound"
                ),
            },
            "process_diagnostics": {
                "role": "explanatory gates, not co-primary equivalence endpoints",
                "metrics": [
                    "valid-program, no-diff, parse, boundary, evaluation-failure",
                    "population insertion/replacement, occupancy, tokens, latency, cost",
                ],
                "rule": (
                    "report paired seed-level rates and investigate any systematic "
                    "difference; do not invent an unsupported universal percentage margin"
                ),
            },
        },
        "benchmark": benchmark_freeze(),
        "model": "deepseek/deepseek-v4-flash",
        "endpoint": "https://openrouter.ai/api/v1",
        "seeds": SEEDS,
        "completed_mutations_per_run": COMPLETED_MUTATIONS,
        "python": sys.version,
        "platform": platform.platform(),
        "environments": {
            "noema": environment_freeze(ROOT / ".venv/bin/python"),
            "stock_experimental": environment_freeze(
                ROOT / ".openevolve-stock/.venv/bin/python"
            ),
        },
        "noema": git_state(ROOT),
        "noema_isolated_source": noema_source,
        "stock_pristine": pristine,
        "stock_experimental": experimental,
        "file_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in files},
        "run_root": str(args.run_root.resolve()),
        "stock_config_values": config_values,
        "launch_commands_not_executed": launch_commands(
            config_dir, args.run_root, args.noema_source.resolve()
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.output}")
    print(json.dumps(manifest["approval_required"], indent=2))
    for family, commands in manifest["launch_commands_not_executed"].items():
        print(f"\n[{family}]")
        print("\n".join(commands))


if __name__ == "__main__":
    main()
