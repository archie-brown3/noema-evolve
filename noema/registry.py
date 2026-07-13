"""Independent store/policy construction for configured substrates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from noema.config import SelectionConfig, SubstrateConfig
from noema.base import SubstrateRuntime
from noema.islands import IslandsStore
from noema.selection.stock_openevolve import StockOpenEvolveSelection
from noema.tree import TreeStore

if TYPE_CHECKING:
    from noema.config import NoemaConfig


NATIVE_POLICIES = {"islands": "stock_openevolve", "tree": "uct"}


def resolve_selection_policy(
    substrate: SubstrateConfig, selection: SelectionConfig
) -> str:
    if selection.policy == "substrate_default":
        return NATIVE_POLICIES[substrate.kind]
    return selection.policy


def build_substrate_runtime(config: "NoemaConfig") -> SubstrateRuntime:
    if config.substrate.kind == "islands":
        store = IslandsStore(config.database)
    elif config.substrate.kind == "tree":
        store = TreeStore(
            steps_per_generation=(
                config.substrate.steps_per_generation
                if config.substrate.steps_per_generation is not None
                else 1
            ),
            feature_dimensions=config.database.feature_dimensions,
        )
    else:
        raise ValueError(f"unknown substrate kind {config.substrate.kind!r}")

    policy_name = resolve_selection_policy(config.substrate, config.selection)
    if policy_name == "stock_openevolve":
        policy = StockOpenEvolveSelection()
    elif policy_name == "boltzmann":
        from noema.selection.boltzmann import BoltzmannSelectionPolicy

        policy = BoltzmannSelectionPolicy.from_config(config.selection)
    elif policy_name == "uct":
        from noema.selection.uct import UCTSelectionPolicy

        policy = UCTSelectionPolicy(
            token_budget=config.budget.total_tokens,
            initial_exploration=config.selection.initial_exploration,
            widening_alpha=config.selection.widening_alpha,
            random_seed=config.selection.seed,
        )
    else:
        raise ValueError(
            f"selection policy {policy_name!r} is unavailable for the implemented stores"
        )

    if config.substrate.steps_per_generation is not None:
        store.steps_per_generation = config.substrate.steps_per_generation
    return SubstrateRuntime(store, policy)
