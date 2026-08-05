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
