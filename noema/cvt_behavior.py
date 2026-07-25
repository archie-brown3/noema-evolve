"""Behavioural feature extraction for the CVT-MAP-Elites substrate.

Ported from LEVI (https://github.com/ttanv/levi), MIT (c) 2025 Temoor Tanveer:
``levi/behavior/features.py`` and ``levi/behavior/extractor.py``.

NOEMA adaptations:
- Feature functions take a code ``str`` (+ parsed AST), not LEVI's ``Program``
  type, so the extractor is decoupled from any program object.
- The dead ``ast.Num`` branches from the donor are removed: ``ast.Constant``
  already covers every numeric literal on Python 3.8+, and ``ast.Num`` only
  raises ``DeprecationWarning`` on 3.12 (noema's runtime) and is removed in
  3.14.  The donor's per-feature ``try/except`` silently zeroed those features
  instead; dropping the branch computes them correctly.
- ``extract`` is pure given ``fixed bounds`` (deterministic mode) — the
  substrate uses that mode so the same code always maps to the same cell,
  honouring the determinism guarantee.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import numpy as np


# ----------------------------------------------------------------------------
# Feature functions.  Each takes (code, tree) and returns a scalar.  Only
# ``compute_code_length`` uses ``code``; the rest read the AST.
# ----------------------------------------------------------------------------

def compute_code_length(code: str, tree: Optional[ast.AST] = None) -> float:
    return float(len(code))


def compute_ast_depth(code: str, tree: ast.AST) -> float:
    def _depth(node: ast.AST) -> int:
        children = list(ast.iter_child_nodes(node))
        return 1 + max((_depth(c) for c in children), default=0)

    return float(_depth(tree))


def compute_cyclomatic_complexity(code: str, tree: ast.AST) -> float:
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
    return float(complexity)


def compute_loop_count(code: str, tree: ast.AST) -> float:
    return float(sum(1 for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While))))


def compute_math_operators(code: str, tree: ast.AST) -> float:
    def _count(node: ast.AST) -> int:
        count = 1 if isinstance(node, (ast.BinOp, ast.UnaryOp)) else 0
        for child in ast.iter_child_nodes(node):
            count += _count(child)
        return count

    return float(_count(tree))


def compute_branch_count(code: str, tree: ast.AST) -> float:
    return float(sum(1 for n in ast.walk(tree) if isinstance(n, ast.If)))


def compute_loop_nesting_max(code: str, tree: ast.AST) -> float:
    def _depth(node: ast.AST, current: int) -> int:
        max_depth = current
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While)):
                max_depth = max(max_depth, _depth(child, current + 1))
            else:
                max_depth = max(max_depth, _depth(child, current))
        return max_depth

    return float(_depth(tree, 0))


def compute_function_def_count(code: str, tree: ast.AST) -> float:
    return float(sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)))


def compute_numeric_literal_count(code: str, tree: ast.AST) -> float:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            count += 1
    return float(count)


def compute_comparison_count(code: str, tree: ast.AST) -> float:
    return float(sum(1 for n in ast.walk(tree) if isinstance(n, ast.Compare)))


def compute_subscript_count(code: str, tree: ast.AST) -> float:
    return float(sum(1 for n in ast.walk(tree) if isinstance(n, ast.Subscript)))


def compute_call_count(code: str, tree: ast.AST) -> float:
    return float(sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)))


def compute_comprehension_count(code: str, tree: ast.AST) -> float:
    return float(
        sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp))
        )
    )


def compute_range_max_arg(code: str, tree: ast.AST) -> float:
    max_val = 0.0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "range":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                        max_val = max(max_val, abs(float(arg.value)))
    return float(max_val)


# Deterministic per-feature (min, max) for fixed-bounds normalisation.
# ponytail: calibration knob — these are realistic ranges for a single evolved
# function; a benchmark whose programs vary outside them will squash into few
# cells. Override via config.substrate.cvt_* / data-driven centroids if the
# archive collapses. Too-wide bounds map everything to one cell.
DEFAULT_FEATURE_BOUNDS: dict[str, tuple[float, float]] = {
    "math_operators": (0.0, 20.0),
    "loop_nesting_max": (0.0, 3.0),
    "comprehension_count": (0.0, 5.0),
    "range_max_arg": (0.0, 500.0),
    "code_length": (0.0, 4000.0),
    "cyclomatic_complexity": (1.0, 15.0),
    "loop_count": (0.0, 8.0),
    "branch_count": (0.0, 10.0),
    "ast_depth": (0.0, 20.0),
    "function_def_count": (0.0, 6.0),
    "numeric_literal_count": (0.0, 30.0),
    "comparison_count": (0.0, 10.0),
    "subscript_count": (0.0, 20.0),
    "call_count": (0.0, 30.0),
}


@dataclass
class FeatureVector:
    values: dict[str, float]

    def to_array(self, feature_names: list[str]) -> list[float]:
        return [self.values.get(name, 0.0) for name in feature_names]

    def __getitem__(self, key: str) -> float:
        return self.values.get(key, 0.0)


class BehaviorExtractor:
    """Compute behavioural features from code with deterministic or adaptive
    normalisation to [0, 1] per feature."""

    BUILT_IN_FEATURES: dict[str, Callable[[str, ast.AST], float]] = {
        "code_length": compute_code_length,
        "ast_depth": compute_ast_depth,
        "cyclomatic_complexity": compute_cyclomatic_complexity,
        "loop_count": compute_loop_count,
        "math_operators": compute_math_operators,
        "branch_count": compute_branch_count,
        "loop_nesting_max": compute_loop_nesting_max,
        "function_def_count": compute_function_def_count,
        "numeric_literal_count": compute_numeric_literal_count,
        "comparison_count": compute_comparison_count,
        "subscript_count": compute_subscript_count,
        "call_count": compute_call_count,
        "comprehension_count": compute_comprehension_count,
        "range_max_arg": compute_range_max_arg,
    }

    # LEVI's default behaviour axes.
    DEFAULT_FEATURES = ("math_operators", "loop_nesting_max", "comprehension_count", "range_max_arg")

    def __init__(self, ast_features: Optional[list[str]] = None) -> None:
        self.features: list[str] = list(ast_features) if ast_features else list(self.DEFAULT_FEATURES)
        unknown = [f for f in self.features if f not in self.BUILT_IN_FEATURES]
        if unknown:
            raise ValueError(f"unknown behaviour features: {unknown}")
        # Welford online stats (adaptive mode).
        self._count = {f: 0 for f in self.features}
        self._mean = {f: 0.0 for f in self.features}
        self._M2 = {f: 0.0 for f in self.features}
        self._fixed_bounds: Optional[dict[str, tuple[float, float]]] = None

    def set_fixed_bounds(self, bounds: dict[str, tuple[float, float]]) -> None:
        """Enable deterministic min-max normalisation. Same code -> same vector."""
        resolved: dict[str, tuple[float, float]] = {}
        for feature in self.features:
            lo, hi = bounds.get(feature, (0.0, 100.0))
            if hi <= lo:
                raise ValueError(f"invalid bounds for {feature}: max {hi} must exceed min {lo}")
            resolved[feature] = (float(lo), float(hi))
        self._fixed_bounds = resolved

    def has_fixed_bounds(self) -> bool:
        return self._fixed_bounds is not None

    def _update_stats(self, feature: str, value: float) -> None:
        self._count[feature] += 1
        delta = value - self._mean[feature]
        self._mean[feature] += delta / self._count[feature]
        self._M2[feature] += delta * (value - self._mean[feature])

    def _get_std(self, feature: str) -> float:
        if self._count[feature] < 2:
            return 1.0
        return max(math.sqrt(self._M2[feature] / (self._count[feature] - 1)), 0.1)

    @staticmethod
    def _zscore_to_01(z: float) -> float:
        z = max(-10.0, min(10.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def _raw(self, code: str) -> Optional[dict[str, float]]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        raw: dict[str, float] = {}
        for feature in self.features:
            try:
                raw[feature] = self.BUILT_IN_FEATURES[feature](code, tree)
            except Exception:
                raw[feature] = 0.0
        return raw

    def extract(self, code: str) -> FeatureVector:
        raw = self._raw(code)
        if raw is None:  # unparseable -> neutral centre, matching LEVI
            return FeatureVector({f: 0.5 for f in self.features})
        values: dict[str, float] = {}
        if self._fixed_bounds is not None:
            for feature in self.features:
                lo, hi = self._fixed_bounds[feature]
                values[feature] = float(np.clip((raw[feature] - lo) / (hi - lo), 0.0, 1.0))
        else:
            for feature in self.features:
                self._update_stats(feature, raw[feature])
                z = (raw[feature] - self._mean[feature]) / self._get_std(feature)
                values[feature] = self._zscore_to_01(z)
        return FeatureVector(values)
