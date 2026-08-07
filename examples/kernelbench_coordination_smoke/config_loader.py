"""Config deep-merge + arm-parity enforcement (task 0112, spec §11).

`config/base.yaml` is the invariant experimental cell; `config/arms/*.yaml`
overlays may set ONLY `coordination.module`. This module refuses a delta
outside that — mechanically, not by convention — both at the raw-overlay
level (before merge) and across the four built configs (after merge, via the
same to_yaml() line-diff the rest of the codebase already uses,
test_noema_controller.py::test_paired_arm_configs_differ_only_in_coordination_module).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from noema.config import NoemaConfig

_HERE = Path(__file__).parent
BASE_CONFIG_PATH = _HERE / "config" / "base.yaml"
ARMS_DIR = _HERE / "config" / "arms"
ARM_NAMES = ("null", "hifo", "pes-faithful", "bandit")

# The ONLY key path an overlay may contain.
_ALLOWED_OVERLAY_SHAPE = {"coordination": {"module"}}


class ArmOverlayError(ValueError):
    """An arm overlay, or the merged configs it produced, set something
    besides coordination.module."""


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate_overlay_shape(overlay: Dict[str, Any], arm: str) -> None:
    for key, value in overlay.items():
        if key not in _ALLOWED_OVERLAY_SHAPE:
            raise ArmOverlayError(
                f"arm overlay {arm!r} sets {key!r} — overlays may set ONLY coordination.module"
            )
        if not isinstance(value, dict):
            raise ArmOverlayError(f"arm overlay {arm!r}: {key!r} must be a mapping")
        extra = set(value) - _ALLOWED_OVERLAY_SHAPE[key]
        if extra:
            raise ArmOverlayError(
                f"arm overlay {arm!r} sets coordination.{sorted(extra)[0]} — "
                "overlays may set ONLY coordination.module"
            )


def load_arm_config(arm: str) -> NoemaConfig:
    if arm not in ARM_NAMES:
        raise ValueError(f"unknown arm {arm!r}; valid arms: {ARM_NAMES}")
    base = _load_yaml(BASE_CONFIG_PATH)
    overlay = _load_yaml(ARMS_DIR / f"{arm}.yaml")
    _validate_overlay_shape(overlay, arm)
    merged = _deep_merge(base, overlay)
    return NoemaConfig.from_dict(merged)


def load_all_arm_configs() -> Dict[str, NoemaConfig]:
    return {arm: load_arm_config(arm) for arm in ARM_NAMES}


def assert_configs_differ_only_in_coordination_module(configs: Dict[str, NoemaConfig]) -> None:
    """Cross-check the BUILT configs, not just the overlay source — catches
    any drift a future base.yaml edit could introduce that the per-overlay
    shape check can't see (e.g. two overlays merging inconsistently)."""
    names: List[str] = list(configs)
    if len(names) < 2:
        return
    reference = configs[names[0]].to_yaml().splitlines()
    for name in names[1:]:
        lines = configs[name].to_yaml().splitlines()
        if len(lines) != len(reference):
            raise ArmOverlayError(
                f"arm {name!r} config has a different line count than {names[0]!r} — "
                "overlays may only change coordination.module"
            )
        for a, b in zip(reference, lines):
            if a != b and ("module" not in a or "module" not in b):
                raise ArmOverlayError(
                    f"arm {name!r} config differs from {names[0]!r} outside "
                    f"coordination.module:\n  {a!r}\n  {b!r}"
                )
