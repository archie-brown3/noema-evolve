"""
Named PES arms (task 0066).

The study's verify-run invariant (spec/LIVE-RUNS.md §4) is that two paired runs
differ in exactly ONE config setting: `coordination.module`. So arm identity
lives in the registry KEY, never in a bundle of sub-options a run config has to
get right — a typo in one knob would otherwise yield a half-faithful arm that
still reports itself as pes-faithful.

Each named arm is a thin subclass of PESPlannerModule that pre-sets its
arm-defining knobs and REFUSES to let a run config override them.
"""

from typing import Any, Dict, Optional

from noema.coordination.pes.module import PESPlannerModule

# Knobs that define which arm this is. A run config may not set them on a named
# variant: silent drift (a "pes-faithful" run that quietly used custom prompts)
# is the failure mode this guard exists to make impossible.
ARM_DEFINING_KNOBS = ("prompt_variant", "executor_mode", "recent_strategies_k")


class _NamedPESArm(PESPlannerModule):
    """Base for the registry-bound PES variants. Subclasses set ARM_DEFAULTS."""

    ARM_KEY: str = ""
    ARM_DEFAULTS: Dict[str, Any] = {}

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        llm=None,
        rng=None,
    ):
        params = dict(config or {})
        overridden = sorted(k for k in ARM_DEFINING_KNOBS if k in params)
        if overridden:
            raise ValueError(
                f"coordination.params may not set {overridden} for arm "
                f"'{self.ARM_KEY}': arm identity is the registry key, not a "
                "parameter. Use a different coordination.module instead."
            )
        params.update(self.ARM_DEFAULTS)
        super().__init__(config=params, llm=llm, rng=rng)


class PESCustomModule(_NamedPESArm):
    """pes-custom: the noema-original planning arm. Lean condensed prompts, the
    plan as a coordination suffix (advisory), the cross-lineage
    "Recently Attempted Elsewhere" block ON, and reflection-seeded retries.

    Run-config side (NOT module params): `retry_on="failure"` (the default).
    """

    ARM_KEY = "pes-custom"
    ARM_DEFAULTS = {
        "prompt_variant": "custom",
        "executor_mode": "advisory",
    }


class PESFaithfulModule(_NamedPESArm):
    """pes-faithful: the declared fidelity anchor. Near-verbatim LoongFlow
    math-agent prompts (planner 0063, summary 0064), the plan as the executor's
    brief (directive, 0065 — the Decision #25 scoped prompt-identity
    exemption), the island status block ON (0061), no recent_block
    (Decision #27), and an empty reflection-seeded retry_advice (0065: LoongFlow
    retries carry evaluation text, not reflections).

    Run-config side (NOT module params, so they must be set in the cell's run
    config): `retry_on="non_improvement"` and `retry_cap=2` (Decision #28).
    """

    ARM_KEY = "pes-faithful"
    ARM_DEFAULTS = {
        "prompt_variant": "faithful",
        "executor_mode": "directive",
        # Decision #27: the cross-lineage digest is custom-only. The faithful
        # planning prompt has no slot for it, so this is belt-and-braces.
        "recent_strategies_k": 0,
    }
