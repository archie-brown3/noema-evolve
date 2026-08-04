"""Offline smoke tests for the bundled quickstart demos (``levi.demos``).

These cover the pure-Python surface — problem text, seed, and scoring logic —
without running the optimizer or hitting the network.
"""

from __future__ import annotations

from levi.demos import aime, circle_packing


def test_circle_packing_surface_is_present():
    assert isinstance(circle_packing.description, str) and circle_packing.description.strip()
    assert isinstance(circle_packing.signature, str) and "run_packing" in circle_packing.signature
    assert isinstance(circle_packing.seed, str) and "def run_packing" in circle_packing.seed


def test_circle_packing_scores_its_own_seed():
    """The seed must be a valid packing with a positive score (a row of n circles)."""
    ns: dict = {}
    exec(circle_packing.seed, ns)
    result = circle_packing.score(ns["run_packing"])
    assert result["valid"] == 1.0
    assert result["score"] > 0.0
    # n=10 circles of radius 1/(2n) -> sum_radii = 0.5
    assert abs(result["score"] - 0.5) < 1e-6


def test_circle_packing_rejects_overlap():
    def overlapping():
        import numpy as np

        centers = np.zeros((circle_packing.N_CIRCLES, 2)) + 0.5
        radii = np.full(circle_packing.N_CIRCLES, 0.4)
        return centers, radii, float(radii.sum())

    result = circle_packing.score(overlapping)
    assert result["score"] == 0.0
    assert result["valid"] == 0.0


def test_aime_surface_is_present():
    assert isinstance(aime.description, str) and aime.description.strip()
    assert isinstance(aime.seed_prompt, str) and aime.seed_prompt.strip()


def test_aime_answer_extraction():
    assert aime._extract_answer(r"The answer is \boxed{204}.") == 204
    assert aime._extract_answer("... so the final answer: 17") == 17
    assert aime._extract_answer("work work\n#### 042") == 42
    assert aime._extract_answer("a tour of 1,234 steps, then 99") == 99
    assert aime._extract_answer("no digits here") is None


def test_aime_score_counts_exact_matches(monkeypatch):
    inputs = [
        {"problem": "p1", "answer": 1},
        {"problem": "p2", "answer": 2},
        {"problem": "p3", "answer": 3},
    ]
    # Stub the task model: gets p1 and p3 right, p2 wrong.
    answers = {"p1": "the answer is 1", "p2": "the answer is 99", "p3": r"\boxed{3}"}
    monkeypatch.setattr(aime, "_solve", lambda prompt, problem: answers[problem])

    result = aime.score("any prompt", inputs)
    assert result["correct"] == 2.0
    assert result["total"] == 3.0
    assert abs(result["score"] - (200.0 / 3.0)) < 1e-9


def test_aime_score_handles_task_model_errors(monkeypatch):
    def boom(prompt, problem):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(aime, "_solve", boom)
    result = aime.score("p", [{"problem": "x", "answer": 5}])
    assert result["score"] == 0.0
