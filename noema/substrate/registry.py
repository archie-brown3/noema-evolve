"""Independent store/policy construction for configured substrates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from noema.config import SelectionConfig, SubstrateConfig
from noema.substrate.base import SubstrateRuntime
from noema.substrate.islands import IslandsStore
from noema.substrate.selection.stock_openevolve import StockOpenEvolveSelection

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
    else:
        raise ValueError("tree substrate is specified by task 0037 and is not implemented")

    policy_name = resolve_selection_policy(config.substrate, config.selection)
    if policy_name == "stock_openevolve":
        policy = StockOpenEvolveSelection()
    elif policy_name == "boltzmann":
        from noema.substrate.selection.boltzmann import BoltzmannSelectionPolicy

        policy = BoltzmannSelectionPolicy.from_config(config.selection)
    else:
        raise ValueError(
            f"selection policy {policy_name!r} is unavailable for the implemented stores"
        )

    if config.substrate.steps_per_generation is not None:
        store.steps_per_generation = config.substrate.steps_per_generation
    return SubstrateRuntime(store, policy)
