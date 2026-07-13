"""
Non-invasive wrapper around evaluator.py: captures whatever it prints (child
tracebacks, subprocess stderr, validation-failure messages) during a failed
evaluation and routes that text into the artifacts side-channel, WITHOUT
editing the shared evaluator.py other benchmarks/runs depend on.

evaluator.py's evaluate() catches every exception internally and only
print()s the diagnostic text — nothing else in the process ever sees it, so
this wrapper captures stdout/stderr for the duration of the call rather than
re-deriving the failure logic. Point --evaluation-file at this file to opt in.
"""

import contextlib
import io

import evaluator as _evaluator
from openevolve.evaluation_result import EvaluationResult


def _is_failure(metrics: dict) -> bool:
    return (not metrics) or metrics.get("validity", 1.0) == 0.0 or "error" in metrics


def evaluate(program_path):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        result = _evaluator.evaluate(program_path)

    if isinstance(result, EvaluationResult):
        metrics, artifacts = result.metrics, result.artifacts
    else:
        metrics, artifacts = result, {}

    if _is_failure(metrics) and not artifacts.get("stderr"):
        return EvaluationResult(metrics=metrics, artifacts={"stderr": buffer.getvalue()})
    return result


def evaluate_stage1(program_path):
    # Cascade evaluation is structurally rejected in every noema config
    # (PLAN.md sec 1.3), so this path is unreachable — passthrough only.
    return _evaluator.evaluate_stage1(program_path)


def evaluate_stage2(program_path):
    return _evaluator.evaluate_stage2(program_path)
