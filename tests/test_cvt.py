"""CVTStore contract + CVT-MAP-Elites behaviour tests (task 0108)."""

import inspect
import json
import tempfile

import numpy as np
from openevolve.database import Program

import noema.cvt as cvt_module
from noema.base import PopulationSnapshot, PopulationStore
from noema.cvt import CVTStore, init_cvt_centroids, nearest_centroid


def prog(pid, code, score, parent=None):
    return Program(id=pid, code=code, parent_id=parent, metrics={"combined_score": score})


LOOPY = "def f():\n    t=0\n    for i in range(1000):\n        for j in range(9): t+=i*j\n    return t\n"
COMPY = "def f():\n    return sum(i*2 for i in range(10))\n"


def make_store(**kw):
    kw.setdefault("n_centroids", 64)
    kw.setdefault("seed", 7)
    kw.setdefault("feature_dimensions", ["x"])
    return CVTStore(**kw)


def test_satisfies_population_store_protocol():
    assert isinstance(make_store(), PopulationStore)


def test_public_interface_contains_no_island_named_members():
    members = [name for name in dir(CVTStore) if not name.startswith("_")]
    assert not any("island" in name.lower() for name in members), members


def test_topology_is_cvt_regions():
    assert make_store().topology == "cvt_regions"
    assert make_store().snapshot().topology == "cvt_regions"


def test_centroids_are_deterministic_pure_function():
    assert np.array_equal(init_cvt_centroids(64, 4, 7), init_cvt_centroids(64, 4, 7))
    # same store config -> identical centroids
    assert np.array_equal(make_store()._centroids, make_store()._centroids)


def test_cell_assignment_is_deterministic():
    s = make_store()
    p = prog("p1", LOOPY, 0.5)
    assert s.cell_of(p) == s.cell_of(p)


def test_elite_replacement_keeps_highest_fitness_per_cell():
    s = make_store()
    s.add(prog("p1", COMPY, 0.5))
    cell = s._cell_of["p1"]
    s.add(prog("p2", COMPY, 0.4))  # same code -> same cell, lower score
    assert s._elite_of[cell] == "p1"
    s.add(prog("p3", COMPY, 0.9))  # higher score -> new elite
    assert s._elite_of[cell] == "p3"


def test_keeps_whole_corpus_never_deletes():
    s = make_store()
    for i in range(5):
        s.add(prog(f"p{i}", COMPY, 0.1 * i))
    assert s.num_programs == 5  # all kept, even displaced ones


def test_best_program_is_global_max():
    s = make_store()
    s.add(prog("p1", COMPY, 0.5))
    s.add(prog("p2", LOOPY, 0.9))
    assert s.best_program().id == "p2"


def test_state_is_json_serializable_and_round_trips_with_snapshot_equality():
    s = make_store()
    s.add(prog("p1", COMPY, 0.5))
    s.add(prog("p2", LOOPY, 0.7))
    state = s.state_dict()
    encoded = json.dumps(state)  # must be JSON-serializable
    restored = make_store()
    restored.load_state_dict(json.loads(encoded))
    assert restored.state_dict() == state
    assert restored.snapshot() == s.snapshot()
    assert restored.snapshot(scope=None) == s.snapshot(scope=None)


def test_checkpoint_file_round_trip():
    s = make_store()
    s.add(prog("p1", COMPY, 0.5))
    with tempfile.TemporaryDirectory() as d:
        s.save(d, iteration=9)
        r = make_store()
        r.load(d)
        assert r.last_iteration == 9 and r.num_programs == 1
        assert np.array_equal(r._centroids, s._centroids)


def test_snapshot_distinguishes_local_and_global_scope():
    s = make_store()
    s.add(prog("p1", COMPY, 0.5))
    cell = s._cell_of["p1"]
    assert isinstance(s.snapshot(scope=cell), PopulationSnapshot)
    assert s.snapshot(scope=None).regions  # global carries regions
    assert not s.snapshot(scope=cell).regions  # local does not


def test_substrate_makes_no_llm_calls():
    """Metering guarantee: the substrate must never make an LLM call."""
    src = inspect.getsource(cvt_module)
    assert "noema.budget" not in src
    assert ".generate(" not in src
    assert "BudgetedLLM" not in src


def test_duplicate_program_id_rejected():
    s = make_store()
    s.add(prog("p1", COMPY, 0.5))
    try:
        s.add(prog("p1", LOOPY, 0.9))
    except ValueError:
        return
    raise AssertionError("expected ValueError on duplicate id")
