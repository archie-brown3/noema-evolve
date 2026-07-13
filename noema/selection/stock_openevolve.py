"""Exact OpenEvolve parent-selection adapter."""

from typing import Any, Dict, Mapping, Optional

from noema.base import PopulationStore, Selection


class StockOpenEvolveSelection:
    required_capabilities = frozenset({"native_stock_selection"})
    supported_hints = frozenset()

    def select(
        self,
        store: PopulationStore,
        *,
        target_scope=None,
        num_inspirations: int = 0,
        hints: Optional[Mapping[str, Any]] = None,
    ) -> Selection:
        # One call only: preserving OpenEvolve's global-random draw stream is
        # part of the scientific compatibility contract.
        return store.native_select(target_scope, num_inspirations)

    def on_child_accepted(self, *, parent, child, step_size: float) -> None:
        return None

    def on_child_rejected(self, *, parent, child=None, eval_failed: bool) -> None:
        return None

    def state_dict(self) -> Dict[str, Any]:
        return {}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        return None
