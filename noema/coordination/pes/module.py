"""
Phase classes live in sibling files (task 0060 split, mirroring LoongFlow's own
planner/ executor/ summary/ layout): planner.py (Planner), executor.py
(Executor), summarizer.py (Summarizer). This façade owns all shared state.

- advise()          <- Planner.plan -> Executor.build_advice: one metered
                       planning call per mutation, plan injected as the
                       standard coordination suffix
                       (LoongFlow: agents/general_agent/planner.py)
- retry_advice()    <- Executor.retry_block: reflection-seeded retries
- report_result()   <- Summarizer.assess + record (pure Python):
                       computes IMPROVEMENT/REGRESSION/STALE vs parent score
                       (agents/general_agent/summary.py:228-247) and enqueues
                       the child for reflection. No LLM call here.
- on_generation_end <- Summarizer.reflect_pending (Phase 2): drains
                       the queue, one metered reflection call per child
                       (summary.py:249-409, Reflexion-derived), storing the
                       causal explanation so the next plan for that lineage
                       sees it (LoongFlow stores generate_plan/summary on every
                       solution: framework/pes/database/database.py:96-101)

Documented deviations from the released code (PLAN.md section 2.2 discipline):
1. One single-shot planning call, not a multi-turn Claude Code agent session.
   LoongFlow's own math_agent runs PES on plain LLM calls, so this recast has
   upstream precedent.
2. The plan is a prompt *suffix* after openevolve's mutation instructions, not
   the executor's primary directive. Biggest fidelity gap — flagged in the fit
   assessment; **closed by Stage 2** ([[tasks/0050-implement-stage2-reflection-seeded-retries]]):
   `retry_advice` now seeds retries with the lineage's causal reflection, making
   planning and execution iterative (plan → attempt → *why it failed* → retry
   informed by the reflection) rather than "plan once, append as static suffix."
3. The parent is sampled by the host and handed in via GenerationContext;
   LoongFlow's planner samples it itself and can query the database with
   tools. Lineage memory here is the module-internal plan/outcome store.
4. Reflection (Phase 2, "PES Phase 2 Plan" Stage 0) runs deferred at the
   generation tick rather than inline in report_result — report_result stays
   sync/no-LLM (its interface contract) and enqueues; on_generation_end (already
   async) makes the metered call. Round-robin island scheduling guarantees a
   lineage's own reflection lands before its next mutation, so this deferral is
   invisible to the one consumer that matters. Assessment stays a pure-Python
   fitness comparison (matches LoongFlow's _assess), only the causal explanation
   is an LLM call (matches _reflect).
5. "Expected Deliverables" is dropped from the plan skeleton: the deliverable
   is fixed by the substrate (a diff/rewrite of the program).
6. The "Recently Attempted Elsewhere" section of the planning prompt is
   noema-original (no LoongFlow precedent): a population-wide, cross-lineage
   digest of recent strategies + outcomes, to stop independent lineages
   converging on the same untested idea ("PES Phase 2 Plan" Design 2).
"""

import logging
from typing import Any, Dict, List, Optional

from noema.coordination.base import Advice, CoordinationModule, GenerationContext, Outcome
from noema.coordination.pes.executor import Executor
from noema.coordination.pes.planner import (  # noqa: F401  (re-exported)
    _HISTORY_TAIL,
    PLANNER_SYSTEM,
    PLANNER_USER_TEMPLATE,
    Planner,
)
from noema.coordination.pes.summarizer import (  # noqa: F401  (re-exported)
    FAILED,
    IMPROVED,
    REFLECTION_SYSTEM,
    REFLECTION_USER_TEMPLATE,
    REGRESSED,
    STALE,
    Summarizer,
)
from noema.views import ProgramView

logger = logging.getLogger(__name__)


class PESPlannerModule(CoordinationModule):
    """LoongFlow-derived planner arm: plan-before-mutate, assess-after."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        llm=None,
        rng=None,
    ):
        super().__init__(config=config, llm=llm, rng=rng)
        self.max_code_chars: int = self.config.get("max_code_chars", 2000)
        # Problem-domain constraints (e.g. "explicit constructor, not iterative
        # search") — orthogonal to search mechanics, which the planner never
        # sees. Populated from NoemaConfig.prompt.system_message by the
        # controller unless the experiment overrides it in coordination.params.
        self.domain_context: str = self.config.get("domain_context", "")
        # Prompt variant (task 0063): "custom" = the lean noema recast
        # (default, byte-identical to pre-0063 behavior); "faithful" = the
        # near-verbatim LoongFlow math-agent port. Task 0066 binds the
        # pes-faithful registry key to it; variant identity lives in the
        # registry key, this knob is the internal switch.
        self.prompt_variant: str = self.config.get("prompt_variant", "custom")
        if self.prompt_variant not in ("custom", "faithful"):
            raise ValueError(
                "pes prompt_variant must be 'custom' or 'faithful', "
                f"got {self.prompt_variant!r}"
            )
        # "advisory" (default): plan rides as the standard coordination suffix,
        # byte-identical to today. "directive" (task 0065, pes-faithful only):
        # the verbatim LoongFlow executor prompt, plan as the primary
        # instruction — the Decision #25 scoped prompt-identity exemption.
        self.executor_mode: str = self.config.get("executor_mode", "advisory")
        if self.executor_mode not in ("advisory", "directive"):
            raise ValueError(
                "pes executor_mode must be 'advisory' or 'directive', "
                f"got {self.executor_mode!r}"
            )
        # Reflection (Phase 2 Stage 0). D1 escape hatches: disable entirely, or
        # cap how many queued children get reflected on per generation tick if
        # the coordination-account spend proves too high in practice.
        self.reflection_enabled: bool = self.config.get("reflection_enabled", True)
        self.max_pending_reflections_per_tick: Optional[int] = self.config.get(
            "max_pending_reflections_per_tick", None
        )
        # Context protection for the faithful brief (task 0064, design note
        # §2.3): only a capped Executive-Summary + Actionable-Guidance slice of
        # the brief re-enters downstream prompts (the full text stays in
        # _plans[child]["reflection_full"]), and a pre-flight size assertion
        # fails loud rather than letting the locked context window overflow.
        self.reflection_slice_max_tokens: int = self.config.get(
            "reflection_slice_max_tokens", 300
        )
        # The substrate's context window, in tokens. MUST match the server the run
        # actually talks to. The old 10240 default was a 14B-era pin that outlived
        # its server: the 2026-07-13 run served a 16384-token window while this
        # value still said 10240, and the guard below correctly refused a prompt
        # that would in fact have fitted (task 0067). Runners pass the real n_ctx.
        self.context_window_tokens: int = self.config.get("context_window_tokens", 16384)
        # Cap on sibling TABLE ROWS in the faithful reflection prompt (task 0067).
        # Every other component of that prompt is bounded — code and stderr by
        # max_code_chars, the parent's brief by the downstream slice — but the
        # sibling table rendered one row per child of the parent with no limit, so
        # the prompt grew without bound as a parent accumulated children and a run
        # that fitted early failed later. The table is a HOST-ADDED field (noema's
        # precomputed recast of LoongFlow's get_childs_by_parent_id tool fetch, which
        # in the donor is an agent-controlled query, not a full dump), so bounding it
        # is a host-side compaction, not an edit to donor prompt text.
        # The reported statistics (rank X, total Y, top score Z) are still computed
        # over the WHOLE family — only the rendered rows are limited, and the
        # truncation is disclosed in the block. None = unbounded (pre-0067).
        self.max_siblings_rendered: Optional[int] = self.config.get(
            "max_siblings_rendered", 20
        )
        # Cross-lineage diversity signal (Phase 2 Stage 0, Design 2). D4 knobs.
        self.recent_strategies_k: int = self.config.get("recent_strategies_k", 3)
        self.strategy_digest_chars: int = self.config.get("strategy_digest_chars", 150)
        # plan/outcome/reflection per child program id — the PES lineage memory
        self._plans: Dict[str, Dict[str, Any]] = {}
        # children awaiting a reflection call, drained at the generation tick
        self._pending_reflections: List[Dict[str, Any]] = []
        # Phase objects (task 0060): share this module's state by reference.
        self._planner = Planner(self)
        self._executor = Executor(self)
        self._summarizer = Summarizer(self)

    # ------------------------------------------------------------- advise

    async def advise(self, ctx: GenerationContext) -> Advice:
        if ctx.parent is None or self.llm is None:
            return Advice()
        plan = await self._planner.plan(ctx)
        if not plan:
            return Advice()
        advice = self._executor.build_advice(plan, ctx)
        # Declared prompt deviation on a non-island substrate (task 0080). None
        # on islands, so the fidelity anchor's attribution is unchanged.
        adaptation = self._planner.topology_adaptation(ctx)
        if adaptation:
            advice.attribution["topology_adaptation"] = adaptation
        return advice

    async def retry_advice(self, ctx: GenerationContext, error_text: str, attempt: int) -> str:
        """Reflection-seeded retries (Design 4) — see Executor.retry_block.
        Directive mode yields "" (see build_retry_prompt instead)."""
        return self._executor.retry_block(ctx, error_text, attempt)

    def build_retry_prompt(
        self, ctx: GenerationContext, attribution: Dict[str, Any], attempt: int, error_text: str
    ) -> Optional[Dict[str, str]]:
        """Directive-mode retry hook (task 0065): the controller duck-types
        this optional method (not part of the CoordinationModule ABC — see
        base.py's docstring on mechanism-specific semantics) to get the FULL
        re-formatted LoongFlow template for a retry, instead of the generic
        suffix path. Returns None when this mutation wasn't built in
        directive mode, so the controller falls back to the standard path."""
        if not attribution.get("full_executor_prompt"):
            return None
        return self._executor.retry_prompt(attempt, error_text)

    def _truncate(self, code: str) -> str:
        if len(code) > self.max_code_chars:
            return code[: self.max_code_chars] + "\n# ... (truncated)"
        return code

    # ------------------------------------------------------- credit / state

    def report_result(
        self,
        ctx: GenerationContext,
        child: Optional[ProgramView],
        attribution: Dict[str, Any],
        eval_failed: bool,
        *,
        outcome: Outcome = Outcome.ACCEPTED,
    ) -> None:
        # `outcome` (task 0090) is accepted for contract conformance but not read:
        # PES keys purely off `child is None` / eval_failed and is unchanged by it.
        plan = attribution.get("plan")
        if ctx.parent is None:
            return
        if child is None:
            return  # no program produced: no lineage node to attach the plan to
        # A failed PLANNING call degrades advise() to a no-op Advice() (empty
        # plan) — but the mutation itself may still have produced a real,
        # evaluated child. Record it anyway (task 0042): dropping it here made
        # the child permanently invisible to _plans, so its whole lineage kept
        # reporting "None — first plan for this lineage" and the cross-lineage
        # diversity digest never saw it. The gaps correlate with cluster
        # transients, i.e. exactly the mechanism this arm is meant to measure.
        # No plan means nothing to reflect on, so reflection stays unqueued.
        self._summarizer.record(ctx, child, plan or "", eval_failed)

    # ------------------------------------------------ reflection (Phase 2)

    async def on_generation_end(self, ctx: GenerationContext) -> None:
        """Drain the deferred reflection queue — see Summarizer.reflect_pending."""
        await self._summarizer.reflect_pending()

    # ----------------------------------------------------------- persistence

    def state_dict(self) -> Dict[str, Any]:
        # D2: persist the pending queue so a checkpoint resume doesn't silently
        # drop children that were enqueued but not yet reflected on.
        return {"plans": self._plans, "pending_reflections": self._pending_reflections}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self._plans = dict(state.get("plans", {}))
        self._pending_reflections = list(state.get("pending_reflections", []))

    def log_snapshot(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        reflections = 0
        for entry in self._plans.values():
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
            if entry.get("reflection"):
                reflections += 1
        return {
            "plans_stored": len(self._plans),
            "outcomes": counts,
            "reflections_stored": reflections,
            "pending_reflections": len(self._pending_reflections),
        }
