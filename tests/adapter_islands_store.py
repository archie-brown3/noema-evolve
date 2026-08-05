"""Strict ProgramDatabase adapter over IslandsStore (0188 Stage 1, widened Stage 7).

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

Stage 7 widened the wrapper until every donor call has a wrapper capability to
route to, so this file grew routes, not logic: nothing here computes, adapts, or
corrects an answer. Where the donor spells a name upstream spells privately
(``_calculate_feature_coords``), the route lands on the wrapper's public name
(``feature_coords``) — the translation is the adapter's job, not the wrapper's.
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
        return self._store.best_program(metric=metric)

    def get_top_programs(
        self, n: int = 10, metric: Optional[str] = None, island_idx: Optional[int] = None
    ):
        return self._store.top_programs(n, island=island_idx, metric=metric)

    def sample(self, num_inspirations: Optional[int] = None):
        return self._store.sample(num_inspirations=num_inspirations)

    def sample_from_island(self, island_id: int, num_inspirations: Optional[int] = None):
        return self._store.sample_from_island(island_id, num_inspirations=num_inspirations)

    def save(self, path: Optional[str] = None, iteration: int = 0) -> None:
        self._store.save(path, iteration)

    def load(self, path: str) -> None:
        self._store.load(path)

    def set_current_island(self, island_idx: int) -> None:
        self._store.set_current_island(island_idx)

    def next_island(self) -> int:
        return self._store.next_island()

    def should_migrate(self) -> bool:
        return self._store.should_migrate()

    def migrate_programs(self) -> None:
        self._store.migrate_programs()

    def log_island_status(self) -> None:
        self._store.log_island_status()

    def store_artifacts(self, program_id: str, artifacts) -> None:
        self._store.store_artifacts(program_id, artifacts)

    def get_artifacts(self, program_id: str):
        return self._store.get_artifacts(program_id)

    # Donor spellings of upstream privates -> the wrapper's public names.
    def _validate_migration_results(self) -> None:
        self._store.validate_migration_results()

    def _calculate_feature_coords(self, program):
        return self._store.feature_coords(program)

    def _feature_coords_to_key(self, coords):
        return self._store.feature_coords_to_key(coords)

    def _calculate_complexity_bin(self, complexity):
        return self._store.complexity_bin(complexity)

    def _calculate_diversity_bin(self, diversity):
        return self._store.diversity_bin(diversity)

    def _fast_code_diversity(self, code1, code2):
        return self._store.code_diversity(code1, code2)

    def _sample_inspirations(self, parent, n: int = 5):
        return self._store.sample_inspirations(parent, n=n)

    # -- upstream state, served live by the wrapper --------------------------
    #
    # These are the CONTAINERS the wrapper owns, handed back unwrapped. Stage 1
    # served them as read-only views rebuilt per access, because the wrapper had
    # no write path; Stage 7 gave it one, and a rebuilt view is now the wrong
    # answer — donor writes (`db.islands[i].add(id)`,
    # `db.island_generations[i] = 4`, `del db.programs[id]`) must land on the
    # real state, exactly as they do upstream.

    @property
    def programs(self):
        return self._store.programs

    @property
    def islands(self):
        return self._store.islands

    @property
    def archive(self):
        return self._store.archive

    @property
    def feature_bins(self):
        return self._store.feature_bins

    @property
    def island_feature_maps(self):
        return self._store.island_feature_maps

    @property
    def island_generations(self):
        return self._store.island_generations

    @property
    def island_best_programs(self):
        return self._store.island_best_programs

    @property
    def last_migration_generation(self):
        return self._store.last_migration_generation

    @property
    def best_program_id(self):
        return self._store.best_program_id

    @property
    def current_island(self):
        return self._store.current_island

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
        # A write is routed only if the WRAPPER has something of that name to
        # write to. Two shapes reach here: a state write (`db.current_island = 1`)
        # lands on the wrapper's property setter, and a live-instance method
        # override (`db.sample_from_island = fn`, or mock.patch.object) shadows
        # the wrapper's method on the store instance — so this adapter's own
        # method still resolves it through the wrapper rather than around it.
        # Anything the wrapper does not know about raises, as before: silently
        # absorbing a write is the same hole as silently serving a read.
        if not hasattr(type(self._store), name):
            raise NotImplementedError(
                f"missing wrapper capability: writing ProgramDatabase.{name} — "
                "the wrapper exposes no attribute of that name"
            )
        try:
            setattr(self._store, name, value)
        except AttributeError as exc:  # read-only property on the wrapper
            raise NotImplementedError(
                f"missing wrapper capability: writing ProgramDatabase.{name} — "
                "the wrapper serves it read-only"
            ) from exc

    def __delattr__(self, name):
        # Donor tests reach here via unittest.mock.patch.object teardown, which
        # deletes the override it installed above.
        try:
            delattr(self._store, name)
        except AttributeError as exc:
            raise NotImplementedError(
                f"missing wrapper capability: deleting ProgramDatabase.{name} — "
                "the wrapper exposes no mutation path for this attribute"
            ) from exc
