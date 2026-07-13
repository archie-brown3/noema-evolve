"""MCTS-AHD-derived UCT parent selection through neutral store capabilities.

Kernel provenance: MCTS-AHD commit
ee9c4f424503c65a5fd2b899e6620ce86079fedb (MIT), ``source/mcts.py``.

NOEMA deviations are deliberate: one child is expanded per host iteration,
exploration decays on metered tokens rather than evaluation count, and exact
ties use program IDs rather than global random state.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

from openevolve.utils.metrics_utils import get_fitness_score

from noema.base import PopulationStore, Selection, TreeTopology


_SCHEMA_VERSION = 1


def uct_score(
    *,
    quality: float,
    min_quality: float,
    max_quality: float,
    parent_visits: int,
    child_visits: int,
    exploration: float,
) -> float:
    """MCTS-AHD equation 5 with a defined equal-quality normalization."""

    values = (quality, min_quality, max_quality, exploration)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("UCT values must be finite")
    if parent_visits < 0 or child_visits <= 0 or exploration < 0:
        raise ValueError("UCT visits and exploration are outside their domains")
    normalized = (
        0.0
        if max_quality == min_quality
        else (quality - min_quality) / (max_quality - min_quality)
    )
    return normalized + exploration * math.sqrt(
        math.log(parent_visits + 1) / child_visits
    )


def should_widen(*, visits: int, child_count: int, alpha: float) -> bool:
    """MCTS-AHD equation 4 using the frozen inclusive boundary."""

    if visits < 0 or child_count < 0 or not math.isfinite(alpha) or alpha <= 0:
        raise ValueError("widening inputs are outside their domains")
    return math.floor(visits**alpha) >= child_count


def budget_exploration(
    *, initial: float, tokens_spent: int, token_budget: int
) -> float:
    """MCTS-AHD equation 7 adapted to Noema's cumulative token clock."""

    if not math.isfinite(initial) or initial < 0:
        raise ValueError("initial exploration must be finite and non-negative")
    if token_budget <= 0:
        return 0.0
    spent = max(0, int(tokens_spent))
    return initial * max(0, token_budget - spent) / token_budget


class UCTSelectionPolicy:
    """Select expansion parents while TreeStore remains topology-only."""

    required_capabilities = frozenset(
        {"tree_topology", "population", "fitness", "elites"}
    )
    supported_hints = frozenset()

    def __init__(
        self,
        token_budget: int,
        initial_exploration: float = 0.1,
        widening_alpha: float = 0.5,
        random_seed: Optional[int] = None,
    ):
        if token_budget <= 0:
            raise ValueError("UCT token_budget must be positive")
        if not math.isfinite(initial_exploration) or initial_exploration < 0:
            raise ValueError("UCT initial_exploration must be finite and non-negative")
        if (
            not math.isfinite(widening_alpha)
            or widening_alpha <= 0
            or widening_alpha > 1
        ):
            raise ValueError("UCT widening_alpha must be finite and in (0, 1]")
        self.token_budget = int(token_budget)
        self.initial_exploration = float(initial_exploration)
        self.widening_alpha = float(widening_alpha)
        # Accepted for configuration compatibility. Scientific tie-breaking is
        # deterministic and intentionally consumes no random stream.
        self.random_seed = random_seed
        self.tokens_spent = 0
        self.visits: Dict[str, int] = {}
        self.qualities: Dict[str, float] = {}
        self._pending_path: list[str] = []
        self._pending_children: Dict[str, list[str]] = {}
        self._pending_feature_dimensions: tuple[str, ...] = ()

    @staticmethod
    def _tree(store: PopulationStore) -> TreeTopology:
        if not isinstance(store, TreeTopology):
            raise ValueError("UCT requires the read-only tree_topology capability")
        return store

    def _sync_statistics(
        self, store: PopulationStore, tree: TreeTopology
    ) -> Dict[str, Any]:
        programs = {program.id: program for program in store.population()}
        root = tree.tree_root_id()
        if root is None:
            raise ValueError("cannot select from an empty tree")
        if root not in programs:
            raise ValueError("tree root is absent from the population")

        unknown_state = (set(self.visits) | set(self.qualities)) - set(programs)
        if unknown_state:
            raise ValueError(
                f"UCT state refers to unknown programs: {sorted(unknown_state)}"
            )

        visits: Dict[str, int] = {}
        qualities: Dict[str, float] = {}
        visiting: set[str] = set()
        visited: set[str] = set()

        def derive(program_id: str) -> None:
            if program_id in visiting:
                raise ValueError("tree topology contains a cycle")
            if program_id in visited:
                return
            if program_id not in programs:
                raise ValueError(f"tree child is absent from population: {program_id}")
            visiting.add(program_id)
            children = tuple(sorted(tree.tree_children(program_id)))
            if len(set(children)) != len(children):
                raise ValueError("tree topology contains duplicate child IDs")
            for child_id in children:
                derive(child_id)
            if children:
                qualities[program_id] = max(qualities[child_id] for child_id in children)
                visits[program_id] = sum(visits[child_id] for child_id in children)
            else:
                quality = float(store.fitness(programs[program_id]))
                if not math.isfinite(quality):
                    raise ValueError("UCT requires finite program fitness")
                qualities[program_id] = quality
                visits[program_id] = 1
            visiting.remove(program_id)
            visited.add(program_id)

        derive(root)
        if visited != set(programs):
            raise ValueError("tree population contains a disconnected program")

        if self.visits and any(
            program_id in self.visits and self.visits[program_id] != value
            for program_id, value in visits.items()
        ):
            raise ValueError("UCT visit state is inconsistent with tree topology")
        if self.qualities and any(
            program_id in self.qualities
            and not math.isclose(
                self.qualities[program_id], value, rel_tol=0.0, abs_tol=1e-12
            )
            for program_id, value in qualities.items()
        ):
            raise ValueError("UCT quality state is inconsistent with tree topology")

        self.visits = visits
        self.qualities = qualities
        return programs

    def select(
        self,
        store: PopulationStore,
        *,
        target_scope=None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection:
        tree = self._tree(store)
        programs = self._sync_statistics(store, tree)
        node = tree.tree_root_id()
        path = [node]
        observed_children: Dict[str, list[str]] = {}
        exploration = budget_exploration(
            initial=self.initial_exploration,
            tokens_spent=self.tokens_spent,
            token_budget=self.token_budget,
        )

        while True:
            children = list(sorted(tree.tree_children(node)))
            observed_children[node] = children
            if not children or should_widen(
                visits=self.visits[node],
                child_count=len(children),
                alpha=self.widening_alpha,
            ):
                break
            minimum = min(self.qualities[child_id] for child_id in children)
            maximum = max(self.qualities[child_id] for child_id in children)
            scores = {
                child_id: uct_score(
                    quality=self.qualities[child_id],
                    min_quality=minimum,
                    max_quality=maximum,
                    parent_visits=self.visits[node],
                    child_visits=self.visits[child_id],
                    exploration=exploration,
                )
                for child_id in children
            }
            best_score = max(scores.values())
            node = min(
                child_id for child_id in children if scores[child_id] == best_score
            )
            path.append(node)

        self._pending_path = list(path)
        self._pending_children = observed_children
        self._pending_feature_dimensions = tuple(store.feature_dimensions)
        parent = programs[node]
        inspirations = tuple(
            program for program in store.elites() if program.id != parent.id
        )[: max(0, num_inspirations)]
        return Selection(parent, inspirations, None, None)

    def _clear_pending(self) -> None:
        self._pending_path = []
        self._pending_children = {}
        self._pending_feature_dimensions = ()

    def on_child_accepted(self, *, parent: Any, child: Any, step_size: float) -> None:
        if not self._pending_path or self._pending_path[-1] != parent.id:
            raise ValueError("accepted child does not match the pending UCT selection")
        if child.parent_id != parent.id:
            raise ValueError("accepted child is not attached to its selected parent")
        if child.id in self.visits or child.id in self.qualities:
            raise ValueError(f"UCT child ID already exists: {child.id}")

        quality = float(
            get_fitness_score(
                child.metrics, list(self._pending_feature_dimensions)
            )
        )
        if not math.isfinite(quality):
            raise ValueError("UCT requires finite child fitness")
        self.qualities[child.id] = quality
        self.visits[child.id] = 1
        self._pending_children[parent.id] = sorted(
            [*self._pending_children[parent.id], child.id]
        )

        for program_id in reversed(self._pending_path):
            children = self._pending_children[program_id]
            self.qualities[program_id] = max(
                self.qualities[child_id] for child_id in children
            )
            self.visits[program_id] = sum(
                self.visits[child_id] for child_id in children
            )
        self._clear_pending()

    def on_child_rejected(
        self, *, parent: Any, child: Any = None, eval_failed: bool
    ) -> None:
        self._clear_pending()

    def set_tokens_spent(self, tokens_spent: int) -> None:
        self.tokens_spent = max(0, int(tokens_spent))

    def state_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "token_budget": self.token_budget,
            "initial_exploration": self.initial_exploration,
            "widening_alpha": self.widening_alpha,
            "tokens_spent": self.tokens_spent,
            "visits": dict(sorted(self.visits.items())),
            "qualities": dict(sorted(self.qualities.items())),
            "pending_path": list(self._pending_path),
            "pending_children": {
                key: list(self._pending_children[key])
                for key in sorted(self._pending_children)
            },
            "pending_feature_dimensions": list(self._pending_feature_dimensions),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        if not state:
            return
        if state.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError(
                f"unsupported UCT state version: {state.get('schema_version')!r}"
            )
        try:
            token_budget = int(state["token_budget"])
            initial = float(state["initial_exploration"])
            alpha = float(state["widening_alpha"])
            tokens_spent = int(state["tokens_spent"])
            raw_visits = state["visits"]
            raw_qualities = state["qualities"]
            pending_path = list(state["pending_path"])
            raw_pending_children = state["pending_children"]
            dimensions = tuple(state["pending_feature_dimensions"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid UCT state shape") from exc
        if token_budget <= 0 or not math.isfinite(initial) or initial < 0:
            raise ValueError("invalid UCT budget or exploration state")
        if not math.isfinite(alpha) or alpha <= 0 or alpha > 1 or tokens_spent < 0:
            raise ValueError("invalid UCT widening or token-clock state")
        if not isinstance(raw_visits, Mapping) or not isinstance(
            raw_qualities, Mapping
        ) or not isinstance(raw_pending_children, Mapping):
            raise ValueError("invalid UCT statistics or pending state")
        if not all(isinstance(key, str) for key in raw_visits) or not all(
            isinstance(key, str) for key in raw_qualities
        ):
            raise ValueError("UCT statistic IDs must be strings")

        visits: Dict[str, int] = {}
        for key, value in raw_visits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("UCT visits must be positive integers")
            visits[key] = value
        qualities: Dict[str, float] = {}
        for key, value in raw_qualities.items():
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("UCT qualities must be finite")
            qualities[key] = numeric
        if set(visits) != set(qualities):
            raise ValueError("UCT visits and qualities must cover the same IDs")
        if not all(isinstance(key, str) for key in pending_path) or not all(
            isinstance(name, str) for name in dimensions
        ):
            raise ValueError("UCT pending path and feature dimensions must be strings")
        pending_children: Dict[str, list[str]] = {}
        for key, value in raw_pending_children.items():
            if not isinstance(key, str) or not isinstance(value, list) or not all(
                isinstance(child_id, str) for child_id in value
            ):
                raise ValueError("invalid UCT pending child state")
            pending_children[key] = list(value)
        if set(pending_children) != set(pending_path):
            raise ValueError("UCT pending children must cover the pending path")
        if any(program_id not in visits for program_id in pending_path) or any(
            child_id not in visits
            for child_ids in pending_children.values()
            for child_id in child_ids
        ):
            raise ValueError("UCT pending state refers to unknown statistics")
        if any(
            pending_path[index + 1] not in pending_children[pending_path[index]]
            for index in range(len(pending_path) - 1)
        ):
            raise ValueError("UCT pending path does not follow pending child links")

        self.token_budget = token_budget
        self.initial_exploration = initial
        self.widening_alpha = alpha
        self.tokens_spent = tokens_spent
        self.visits = visits
        self.qualities = qualities
        self._pending_path = pending_path
        self._pending_children = pending_children
        self._pending_feature_dimensions = dimensions
