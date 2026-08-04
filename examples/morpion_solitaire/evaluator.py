"""
Evaluator for the Morpion Solitaire (4D) example.

The evolved program is C++ (it drives OpenSpiel's real `morpion_solitaire`
engine), so this evaluator compiles it against the prebuilt OpenSpiel shared
library and runs the resulting binary, following the compile-then-run pattern
of openevolve's `tsp_tour_minimization` example.

The score ceiling (35, proven optimal for the 4D variant) is NOT hardcoded
here: the program prints the engine's own `MaxUtility()` and we normalise by
that, so the ceiling lives in exactly one place.

Setup: the OpenSpiel shared library must be built once beforehand --
    cd .openspiel-reference/open_spiel
    ./install.sh
    mkdir -p build && cd build
    BUILD_SHARED_LIB=ON cmake ../open_spiel -DCMAKE_BUILD_TYPE=Release \
        -DOPEN_SPIEL_BUILD_WITH_PYTHON=OFF -DBUILD_SHARED_LIB=ON
    make -j"$(nproc)" open_spiel
"""

import os
import pathlib
import re
import subprocess
import tempfile
import time
import traceback

# F_imm: locations of the prebuilt OpenSpiel reference build.
_HERE = pathlib.Path(__file__).resolve().parent
_OPENSPIEL_ROOT = _HERE / ".openspiel-reference" / "open_spiel"
_ABSL_INCLUDE = _OPENSPIEL_ROOT / "open_spiel" / "abseil-cpp"
_JSON_INCLUDE = _OPENSPIEL_ROOT / "open_spiel" / "json" / "include"
_LIB_DIR = _OPENSPIEL_ROOT / "build"

COMPILE_TIMEOUT = 180.0
RUN_TIMEOUT = 60.0

_FAILURE = {
    "moves_achieved": 0.0,
    "max_utility": 0.0,
    "target_ratio": 0.0,
    "eval_time": 0.0,
    "combined_score": 0.0,
}


def _compile(program_path, output_path):
    """Compile the evolved .cpp against the prebuilt OpenSpiel library.

    Returns None on success, or an error string on failure.
    """
    if not _LIB_DIR.exists():
        return f"OpenSpiel build directory not found: {_LIB_DIR} (see module docstring)"

    cmd = [
        "g++",
        "-std=gnu++17",
        "-O3",
        "-DNDEBUG",
        "-funroll-loops",
        "-I",
        str(_OPENSPIEL_ROOT),
        "-I",
        str(_ABSL_INCLUDE),
        "-I",
        str(_JSON_INCLUDE),
        str(program_path),
        "-o",
        str(output_path),
        "-L",
        str(_LIB_DIR),
        "-lopen_spiel",
        f"-Wl,-rpath,{_LIB_DIR}",
        "-lpthread",
        "-lm",
        "-ldl",
    ]

    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=COMPILE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return f"Compilation timed out after {COMPILE_TIMEOUT}s"

    if proc.returncode != 0:
        return f"Compilation failed:\n{proc.stderr.decode(errors='replace')[:4000]}"
    return None


def _parse(stdout):
    """Pull MAX_UTILITY and MOVES_ACHIEVED out of the program's stdout."""
    values = {}
    for key in ("MAX_UTILITY", "MOVES_ACHIEVED"):
        match = re.search(rf"^{key}:\s*([-+0-9.eE]+)\s*$", stdout, re.MULTILINE)
        if match is None:
            return None, f"Missing {key} in program output"
        try:
            values[key] = float(match.group(1))
        except ValueError:
            return None, f"Unparseable {key}: {match.group(1)!r}"
    return values, None


def evaluate(program_path):
    """Compile and run the evolved program once; score it against the ceiling.

    The game is deterministic, so a single playthrough is sufficient. Never
    raises: any failure scores 0.
    """
    start_time = time.time()
    binary_path = None

    try:
        with tempfile.NamedTemporaryFile(prefix="morpion_", delete=False) as handle:
            binary_path = handle.name

        error = _compile(program_path, binary_path)
        if error is not None:
            print(error)
            return dict(_FAILURE, eval_time=float(time.time() - start_time))

        try:
            proc = subprocess.run(
                [binary_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=RUN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(f"Program timed out after {RUN_TIMEOUT}s")
            return dict(_FAILURE, eval_time=float(time.time() - start_time))

        stdout = proc.stdout.decode(errors="replace")
        if proc.returncode != 0:
            print(
                f"Program exited with code {proc.returncode}\n"
                f"{proc.stderr.decode(errors='replace')[:2000]}"
            )
            return dict(_FAILURE, eval_time=float(time.time() - start_time))

        values, error = _parse(stdout)
        if error is not None:
            print(f"{error}\nProgram stdout was:\n{stdout[:2000]}")
            return dict(_FAILURE, eval_time=float(time.time() - start_time))

        moves_achieved = values["MOVES_ACHIEVED"]
        max_utility = values["MAX_UTILITY"]
        eval_time = time.time() - start_time

        if max_utility <= 0:
            print(f"Nonsensical MAX_UTILITY from program: {max_utility}")
            return dict(_FAILURE, eval_time=float(eval_time))

        target_ratio = moves_achieved / max_utility
        print(
            f"Evaluation: moves_achieved={moves_achieved:.0f}, "
            f"max_utility={max_utility:.0f}, ratio={target_ratio:.6f}, "
            f"time={eval_time:.2f}s"
        )

        return {
            "moves_achieved": float(moves_achieved),
            "max_utility": float(max_utility),
            "target_ratio": float(target_ratio),
            "eval_time": float(eval_time),
            "combined_score": float(target_ratio),
        }

    except Exception as exc:  # noqa: BLE001 - evaluator must never propagate
        print(f"Evaluation failed completely: {exc}")
        traceback.print_exc()
        return dict(_FAILURE, eval_time=float(time.time() - start_time))

    finally:
        if binary_path and os.path.exists(binary_path):
            os.unlink(binary_path)


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else str(_HERE / "initial_program.cpp")
    print(evaluate(target))
