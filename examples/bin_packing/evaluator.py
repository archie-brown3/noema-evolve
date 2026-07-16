"""
Evaluator for the ONLINE bin-packing benchmark (task 0091).

Runs the candidate program in a subprocess with a wall-clock timeout and a memory
rlimit (the Evaluator is NOT a sandbox), then scores it. The candidate returns
`(bins_used, lower_bound)` per Weibull instance; the score is the mean excess of
bins over the lower bound, mapped to (0, 1] by `1 / (1 + mean_excess)`. Validity
is guaranteed by construction — the fixed online arrival loop only ever places an
item in a bin that fits — so the evaluator does not re-validate placements; it
only guards against a heuristic that crashes or overruns its budget.

Baseline: the initial best-fit heuristic scores ~0.956 (≈4.6% excess on these
Weibull instances, in line with FunSearch's published best-fit gap), leaving real
headroom for an evolved heuristic to close.
"""
import os
import pickle
import resource
import subprocess
import sys
import tempfile
import time

import numpy as np


class TimeoutError(Exception):
    pass


def set_memory_limit(memory_limit_mb):
    """Set an address-space rlimit for the subprocess (bytes)."""
    limit = memory_limit_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run_with_resource_limits(program_path, timeout_seconds=30, memory_limit_mb=512):
    """Run the candidate in a separate process with a timeout + memory cap.

    Returns the list of (bins_used, lower_bound) tuples the program produced,
    one per instance. Raises TimeoutError on overrun and RuntimeError on any
    in-program error — the harness process itself always survives.
    """
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as temp_file:
        script = f"""
import os
import pickle
import resource
import traceback

def set_memory_limit_mb(mb):
    limit = mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

set_memory_limit_mb({memory_limit_mb})

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("program", {program_path!r})
    program = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(program)

    results = program.run_bin_packing(42)
    # Normalise to a list of (int, int) tuples.
    results = [(int(b), int(lb)) for (b, lb) in results]
    with open({temp_file.name!r} + ".results", "wb") as f:
        pickle.dump({{"results": results}}, f)
except Exception as e:
    traceback.print_exc()
    with open({temp_file.name!r} + ".results", "wb") as f:
        pickle.dump({{"error": str(e)}}, f)
"""
        temp_file.write(script)
        temp_file_path = temp_file.name

    results_path = f"{temp_file_path}.results"
    try:
        process = subprocess.Popen(
            [sys.executable, temp_file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=lambda: set_memory_limit(memory_limit_mb),
        )
        try:
            _, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Process timed out after {timeout_seconds} seconds")

        if not os.path.exists(results_path):
            raise RuntimeError(
                f"Results file not found (exit {process.returncode}): "
                f"{stderr.decode()[:500]}"
            )
        with open(results_path, "rb") as f:
            payload = pickle.load(f)
        if "error" in payload:
            raise RuntimeError(f"Program execution failed: {payload['error']}")
        return payload["results"]
    finally:
        for path in (temp_file_path, results_path):
            if os.path.exists(path):
                os.unlink(path)


def _timeout_metrics(reason):
    return {
        "combined_score": 0.0,
        "mean_excess": float("inf"),
        "bins_used": float("inf"),
        "lower_bound": 0.0,
        "num_instances": 0,
        "validity": 0.0,
        "error": reason,
    }


def evaluate(program_path):
    """Score the candidate: combined_score = 1 / (1 + mean_excess)."""
    try:
        start = time.time()
        results = run_with_resource_limits(
            program_path, timeout_seconds=60, memory_limit_mb=512
        )
        eval_time = time.time() - start

        if not results:
            return _timeout_metrics("no instances returned")

        excesses = []
        total_bins = 0
        total_lb = 0
        for bins_used, lower_bound in results:
            if lower_bound <= 0 or bins_used < lower_bound:
                # A packing below the material lower bound is impossible → invalid.
                return {**_timeout_metrics("invalid packing"), "validity": 0.0}
            excesses.append((bins_used - lower_bound) / lower_bound)
            total_bins += bins_used
            total_lb += lower_bound

        mean_excess = float(np.mean(excesses))
        combined_score = 1.0 / (1.0 + mean_excess)

        print(
            f"Evaluation: instances={len(results)}, bins={total_bins}, "
            f"lower_bound={total_lb}, mean_excess={mean_excess:.4f}, "
            f"score={combined_score:.4f}, time={eval_time:.2f}s"
        )
        return {
            "combined_score": float(combined_score),
            "mean_excess": mean_excess,
            "bins_used": float(total_bins),
            "lower_bound": float(total_lb),
            "num_instances": len(results),
            "eval_time": float(eval_time),
            "validity": 1.0,
        }
    except TimeoutError as e:
        print(f"Evaluation timed out: {e}")
        return _timeout_metrics("timeout")
    except Exception as e:
        print(f"Evaluation error: {e}")
        return _timeout_metrics(str(e))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("program", nargs="?", default="initial_program.py")
    args = ap.parse_args()
    print(evaluate(args.program))
