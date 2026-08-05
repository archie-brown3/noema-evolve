"""Strict ProgramDatabase adapter over IslandsStore (0188 Stage 1).

Presents the API the vendored OpenEvolve donor tests expect, implemented
ENTIRELY by calling Noema's wrapper (``IslandsStore``/``SubstrateDatabase``).
Contract (canonical method note §3; code-grounded design in vault note
"0188 OpenEvolve Fidelity Spec §3 — Strict Adapter — 2026-08-05"):

1. Never fall through to ``store._db`` — no reference to the raw upstream
   ``ProgramDatabase`` appears anywhere in this class, and
   ``test_adapter_instrumentation.py`` asserts that statically.
2. Any attribute this adapter cannot serve from the wrapper raises
   immediately, naming the missing wrapper capability — reads via
   ``__getattr__``, writes via ``__setattr__`` (donor tests also mutate
   attributes like ``current_island`` directly; silently absorbing such a
   write would be the same hole as silently serving a read).
"""

from typing import Optional

from openevolve.config import DatabaseConfig

from noema.substrates.islands import IslandsStore


def _unserved_mutation(capability: str):
    def raiser(self, *args, **kwargs):
        raise NotImplementedError(
            f"missing wrapper capability: mutating ProgramDatabase.{capability} — "
            "this is a derived read-only view rebuilt from IslandsStore queries; "
            "the wrapper exposes no mutation path for it"
        )

    return raiser


class _DerivedIdSet(frozenset):
    """``db.islands[i]``. Absorbing a donor mutation on a throwaway copy would
    surface later as a misleading ASSERTION mismatch (which the triage method
    reads as a real-Noema-bug signal); raise at the mutation instead."""

    add = discard = remove = pop = clear = update = _unserved_mutation("islands[i]")


class _DerivedProgramMap(dict):
    """``db.programs``. Same rule as ``_DerivedIdSet``."""

    __setitem__ = __delitem__ = pop = popitem = clear = update = setdefault = (
        _unserved_mutation("programs")
    )


class AdapterProgramDatabase:
    """Stands in for ``openevolve.database.ProgramDatabase`` in donor tests."""

    def __init__(self, config: DatabaseConfig):
        object.__setattr__(self, "_store", IslandsStore(config))

    # -- servable surface (spec §3 serving-call map) -------------------------

    def add(self, program, iteration: int = None, target_island: Optional[int] = None) -> str:
        return self._store.add(program, iteration=iteration, target_island=target_island)

    def get(self, program_id: str):
        return self._store.get(program_id)

    def get_best_program(self, metric: Optional[str] = None):
        if metric is not None:
            raise NotImplementedError(
                "missing wrapper capability: get_best_program(metric=...) — "
                "IslandsStore.best_program() takes no metric argument"
            )
        return self._store.best_program()

    def get_top_programs(
        self, n: int = 10, metric: Optional[str] = None, island_idx: Optional[int] = None
    ):
        if metric is not None:
            raise NotImplementedError(
                "missing wrapper capability: get_top_programs(metric=...) — "
                "IslandsStore.top_programs() takes no metric argument"
            )
        return self._store.top_programs(n, scope=island_idx)

    def sample_from_island(self, island_id: int, num_inspirations: Optional[int] = None):
        return self._store.sample_from_island(island_id, num_inspirations=num_inspirations)

    def save(self, path: Optional[str] = None, iteration: int = 0) -> None:
        self._store.save(path, iteration)

    def load(self, path: str) -> None:
        self._store.load(path)

    @property
    def islands(self):
        # Derived read-only view, endorsed verbatim by the canonical method
        # note §3 routing table: db.islands[1] -> {x.id for x in store.population(1)}.
        # Rebuilt per access; donor MUTATIONS raise immediately (see _DerivedIdSet).
        return [
            _DerivedIdSet(program.id for program in self._store.population(scope))
            for scope in self._store.scopes
        ]

    @property
    def programs(self):
        # Same derived-view rule as `islands`: dict rebuilt from the wrapper's
        # global population on every access, read path only.
        return _DerivedProgramMap(
            (program.id, program) for program in self._store.population(None)
        )

    @property
    def best_program_id(self):
        # Derived read: the id of the wrapper's current best, None when empty.
        best = self._store.best_program()
        return None if best is None else best.id

    @property
    def island_best_programs(self):
        # Derived read-only view (List[Optional[str]], upstream's shape):
        # per-island best id via the wrapper's own per-scope query. NOTE this
        # serves the CURRENT best; upstream tracks incrementally and can hold
        # stale entries — donor tests asserting staleness quirks fail loud
        # here and are triaged as declared deviations, not silently mimicked.
        result = []
        for scope in self._store.scopes:
            top = self._store.top_programs(1, scope=scope)
            result.append(top[0].id if top else None)
        return result

    @property
    def config(self) -> DatabaseConfig:
        # Same object identity as construction arg — donor tests mutate config
        # fields post-construction and expect the store to observe them. This
        # identity is correct; only method-call identity-forwarding is the trap
        # (spec §3 "the known trap, confirmed in-checkout").
        return self._store.config

    # -- everything else fails loudly (hard rule 2) --------------------------

    def __getattr__(self, name):
        raise NotImplementedError(f"missing wrapper capability: ProgramDatabase.{name}")

    def __setattr__(self, name, value):
        raise NotImplementedError(
            f"missing wrapper capability: writing ProgramDatabase.{name} — "
            "the wrapper exposes no mutation path for this attribute"
        )

    def __delattr__(self, name):
        # Donor tests reach here via unittest.mock.patch.object teardown; without
        # this the failure surfaces as a bare AttributeError from mock's own
        # __exit__, hiding which capability was actually missing.
        raise NotImplementedError(
            f"missing wrapper capability: deleting ProgramDatabase.{name} — "
            "the wrapper exposes no mutation path for this attribute"
        )
