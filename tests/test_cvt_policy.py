"""CVTSelectionPolicy (UCB over cells) tests (task 0108)."""

import json

from openevolve.database import Program

from noema.base import SelectionPolicy
from noema.cvt import CVTStore
from noema.selection.cvt import CVTSelectionPolicy


def prog(pid, code, score):
    return Program(id=pid, code=code, metrics={"combined_score": score})


CODES = [
    "def f():\n    return 1\n",
    "def f():\n    return sum(i for i in range(10))\n",
    "def f():\n    t=0\n    for i in range(50):\n        for j in range(5): t+=i\n    return t\n",
]


def seeded_store():
    s = CVTStore(n_centroids=128, seed=3, feature_dimensions=["x"])
    for i, code in enumerate(CODES):
        s.add(prog(f"p{i}", code, 0.3 + 0.2 * i))
    return s


def test_policy_satisfies_protocol_and_capabilities():
    assert isinstance(CVTSelectionPolicy(), SelectionPolicy)
    assert CVTSelectionPolicy.required_capabilities <= CVTStore.capabilities


def test_empty_archive_raises():
    empty = CVTStore(n_centroids=32, seed=1, feature_dimensions=["x"])
    try:
        CVTSelectionPolicy(seed=1).select(empty)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError on empty archive")


def test_selection_is_deterministic_under_seed():
    s = seeded_store()
    a = CVTSelectionPolicy(seed=5)
    b = CVTSelectionPolicy(seed=5)
    for _ in range(4):
        sa = a.select(s, num_inspirations=1)
        sb = b.select(s, num_inspirations=1)
        assert sa.parent.id == sb.parent.id
        assert [i.id for i in sa.inspirations] == [i.id for i in sb.inspirations]
        a.on_child_rejected(parent=sa.parent, eval_failed=False)
        b.on_child_rejected(parent=sb.parent, eval_failed=False)


def test_ucb_stats_update_and_success_rate_influences_choice():
    s = seeded_store()
    pol = CVTSelectionPolicy(seed=2)
    sel = pol.select(s)
    cell = sel.source_scope
    pol.on_child_accepted(parent=sel.parent, child=None, step_size=1.0)
    assert pol._stats[cell] == [1, 1]
    sel2 = pol.select(s)
    pol.on_child_rejected(parent=sel2.parent, eval_failed=True)
    assert pol._stats[sel2.source_scope][0] >= 1


def test_state_dict_round_trips():
    s = seeded_store()
    pol = CVTSelectionPolicy(seed=8)
    sel = pol.select(s)
    pol.on_child_accepted(parent=sel.parent, child=None, step_size=1.0)
    state = json.loads(json.dumps(pol.state_dict()))
    restored = CVTSelectionPolicy(seed=999)
    restored.load_state_dict(state)
    assert restored._stats == pol._stats
    # RNG restored -> next draw matches
    assert restored.select(s).parent.id == pol.select(s).parent.id


def test_composes_in_substrate_runtime():
    from noema.base import SubstrateRuntime
    rt = SubstrateRuntime(seeded_store(), CVTSelectionPolicy(seed=4))
    assert rt.select(num_inspirations=2).parent is not None
