import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "experiments/0132/analyze.py"
SPEC = importlib.util.spec_from_file_location("analyze_0132", MODULE_PATH)
analyze = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(analyze)


def rows(values):
    """One terminal row per request; `None` means no valid evaluation."""
    return [
        {
            "mutation_index": i,
            "completed_mutation_request": True,
            "metrics": None if v is None else {"mean_excess_bins": v},
        }
        for i, v in enumerate(values)
    ]


def test_best_so_far_is_monotone_and_floored_by_baseline():
    curve = analyze.best_so_far(rows([19.0, 17.0, 18.0, 16.5]), baseline=18.4)
    assert curve == [18.4, 17.0, 17.0, 16.5]


def test_error_metrics_do_not_enter_the_best_so_far_curve():
    # Stock admits evaluator-error programs with {"error": 0.0} metrics.
    error_rows = rows([17.0, None])
    error_rows[1]["metrics"] = {"error": 0.0}
    assert analyze.best_so_far(error_rows, baseline=18.4) == [17.0, 17.0]


def test_infinite_mean_excess_is_ignored():
    assert analyze.best_so_far(rows([float("inf")]), baseline=18.4) == [18.4]


def summaries(endpoints, expected=2):
    return {
        seed: analyze.summarize_run(rows([v, v]), baseline=18.4, expected=expected)
        for seed, v in endpoints.items()
    }


def test_verdict_passes_when_five_paired_seeds_are_within_margin():
    left = summaries({42: 10.0, 43: 10.1, 44: 9.9, 45: 10.0, 46: 10.2})
    right = summaries({42: 10.1, 43: 10.0, 44: 10.0, 45: 10.1, 46: 10.1})
    paired = analyze.pair(left, right, margin=0.2)
    assert paired["within_margin"]
    result = analyze.verdict({"a": left, "b": right}, paired, margin=0.2, expected=2)
    assert result["verdict"] == "pass"
    assert result["process_review_required"] is True


def test_verdict_fails_when_one_seed_exceeds_the_margin():
    left = summaries({42: 10.0, 43: 10.0, 44: 10.0, 45: 10.0, 46: 11.0})
    right = summaries({42: 10.0, 43: 10.0, 44: 10.0, 45: 10.0, 46: 10.0})
    paired = analyze.pair(left, right, margin=0.2)
    result = analyze.verdict({"a": left, "b": right}, paired, margin=0.2, expected=2)
    assert result["verdict"] == "fail"


def test_verdict_inconclusive_when_a_run_is_short():
    left = summaries({42: 10.0, 43: 10.0, 44: 10.0, 45: 10.0, 46: 10.0}, expected=3)
    right = summaries({42: 10.0, 43: 10.0, 44: 10.0, 45: 10.0, 46: 10.0}, expected=3)
    paired = analyze.pair(left, right, margin=0.2)
    result = analyze.verdict({"a": left, "b": right}, paired, margin=0.2, expected=3)
    assert result["verdict"] == "inconclusive"


def test_verdict_inconclusive_with_fewer_than_five_paired_seeds():
    left = summaries({42: 10.0, 43: 10.0})
    right = summaries({42: 10.0, 43: 10.0})
    paired = analyze.pair(left, right, margin=0.2)
    result = analyze.verdict({"a": left, "b": right}, paired, margin=0.2, expected=2)
    assert result["verdict"] == "inconclusive"
