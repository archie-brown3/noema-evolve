"""UCB parent-selection policy for the CVT-MAP-Elites substrate.

Ported from LEVI's ``UCBSampler`` (https://github.com/ttanv/levi, MIT (c) 2025
Temoor Tanveer): UCB1 over cell *acceptance rate* (not raw score), so cells that
actually improve the archive are favoured over high-scoring cells that only
produce rejected clones.

NOEMA: this is the ``SelectionPolicy`` peer to the ``CVTStore`` (Decision #33 —
store and policy are separate); it reads occupied cells through the neutral
store surface (``regions``/``elites``) and never touches store internals.  A
single seeded RNG makes selection + inspiration draws deterministic.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Mapping, Optional

from noema.base import PopulationStore, Selection


class CVTSelectionPolicy:
    required_capabilities = frozenset({"cvt_cells"})
    supported_hints = frozenset()

    def __init__(self, *, seed: Optional[int] = None, c: float = 2.0):
        self._rng = random.Random(seed)
        self._c = float(c)
        # cell index -> [n_samples, n_successes]
        self._stats: Dict[int, List[int]] = {}
        self._pending_cell: Optional[int] = None

    def _ucb(self, cell: int, total: int) -> float:
        stats = self._stats.get(cell)
        if stats is None or stats[0] == 0:
            return math.inf  # unexplored cells first
        n_samples, n_successes = stats
        exploitation = n_successes / n_samples
        exploration = self._c * math.sqrt(math.log(total + 1) / n_samples)
        return exploitation + exploration

    def select(
        self,
        store: PopulationStore,
        *,
        target_scope=None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection:
        # Only scopes with a real elite are selectable candidates. Task 0111's
        # grouped regions() always reports every region (a fixed-size scope
        # space, even ones with zero members early in a run) — filter here
        # rather than there, so regions() keeps reporting the true archive
        # shape and any policy stays robust to a sparse/empty candidate.
        cells = [region.scope for region in store.regions() if region.size > 0]
        if not cells:
            raise RuntimeError("CVT archive is empty; seed a program before selecting")

        total = sum(s[0] for s in self._stats.values())
        # Deterministic: max UCB, ties broken by lowest cell index.
        best_cell = max(cells, key=lambda cell: (self._ucb(cell, total), -cell))

        parent_elites = store.elites(best_cell)
        if not parent_elites:
            raise RuntimeError(f"CVT cell {best_cell} has no elite")
        parent = parent_elites[0]

        inspirations = []
        if num_inspirations > 0:
            others = [cell for cell in cells if cell != best_cell]
            self._rng.shuffle(others)
            for cell in others[:num_inspirations]:
                elite = store.elites(cell)
                if elite:
                    inspirations.append(elite[0])

        self._pending_cell = best_cell
        self._stats.setdefault(best_cell, [0, 0])
        return Selection(
            parent=parent,
            inspirations=tuple(inspirations),
            source_scope=best_cell,
            target_scope=best_cell,
        )

    def _record(self, success: bool) -> None:
        if self._pending_cell is None:
            return
        stats = self._stats.setdefault(self._pending_cell, [0, 0])
        stats[0] += 1
        if success:
            stats[1] += 1
        self._pending_cell = None

    def on_child_accepted(self, *, parent, child, step_size: float) -> None:
        self._record(True)

    def on_child_rejected(self, *, parent, child=None, eval_failed: bool) -> None:
        self._record(False)

    def state_dict(self) -> Dict[str, Any]:
        return {
            "stats": {str(cell): list(vals) for cell, vals in sorted(self._stats.items())},
            "pending_cell": self._pending_cell,
            "rng_state": _encode_rng(self._rng.getstate()),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self._stats = {int(cell): [int(vals[0]), int(vals[1])]
                       for cell, vals in state.get("stats", {}).items()}
        self._pending_cell = state.get("pending_cell")
        rng_state = state.get("rng_state")
        if rng_state is not None:
            self._rng.setstate(_decode_rng(rng_state))


def _encode_rng(state: Any) -> Any:
    # random.getstate() -> (version, tuple-of-ints, gauss) ; JSON-safe as lists.
    version, internal, gauss = state
    return [version, list(internal), gauss]


def _decode_rng(state: Any) -> Any:
    version, internal, gauss = state
    return (version, tuple(internal), gauss)
