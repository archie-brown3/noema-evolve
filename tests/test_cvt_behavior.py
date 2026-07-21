"""Tests for the CVT behavioural feature extractor (ported from LEVI)."""

import ast

from noema.cvt_behavior import (
    BehaviorExtractor,
    compute_numeric_literal_count,
    compute_range_max_arg,
    compute_math_operators,
)

SAMPLE = (
    "def f():\n"
    "    x = 5\n"
    "    for i in range(100):\n"
    "        x = x + 2 * i\n"
    "    return x\n"
)


def test_numeric_literals_counted_not_zeroed():
    """The donor's dead ast.Num branch silently zeroed this on 3.12+; the
    ast.Constant path must actually count the literals (5, 100, 2)."""
    tree = ast.parse(SAMPLE)
    assert compute_numeric_literal_count(SAMPLE, tree) == 3.0


def test_range_max_arg_and_math_operators():
    tree = ast.parse(SAMPLE)
    assert compute_range_max_arg(SAMPLE, tree) == 100.0
    assert compute_math_operators(SAMPLE, tree) >= 2.0  # x+..., 2*i


def test_fixed_bounds_is_deterministic():
    ex = BehaviorExtractor(["math_operators", "range_max_arg"])
    ex.set_fixed_bounds({"math_operators": (0, 10), "range_max_arg": (0, 1000)})
    assert ex.extract(SAMPLE).values == ex.extract(SAMPLE).values


def test_fixed_bounds_clips_to_unit_interval():
    ex = BehaviorExtractor(["range_max_arg"])
    ex.set_fixed_bounds({"range_max_arg": (0, 50)})
    # raw 100 with upper bound 50 must clip to 1.0, not exceed it
    assert ex.extract(SAMPLE).values["range_max_arg"] == 1.0


def test_unparseable_code_maps_to_neutral_centre():
    ex = BehaviorExtractor(["math_operators", "range_max_arg"])
    ex.set_fixed_bounds({"math_operators": (0, 10), "range_max_arg": (0, 1000)})
    assert ex.extract("def (:").values == {"math_operators": 0.5, "range_max_arg": 0.5}


def test_unknown_feature_rejected():
    try:
        BehaviorExtractor(["not_a_feature"])
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown feature")
