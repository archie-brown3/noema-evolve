"""Shallow mutation transport for agent-host ``IterationRunner`` integration (0171)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

from noema.agenthost.materialize import materialize_child_code
from noema.agenthost.mutation import MutationRequest, MutationResult, mutation_layout

if TYPE_CHECKING:
    from noema.agenthost.mutation import MutationLayout
    from noema.agenthost.session import AgentSession


@runtime_checkable
class MutationTransport(Protocol):
    """Duck-type ``BudgetedLLM.generate_with_context`` for ``IterationRunner``."""

    iteration: Optional[int]

    async def generate_with_context(
        self,
        system_message: str,
        messages: List[Dict[str, str]],
        model: Any = None,
    ) -> str: ...


class MutationTransportFailure(RuntimeError):
    """A retriable failure from the agent mutation transport."""


class AgentMutationTransport:
    """Wraps ``mutation_backend`` for ``IterationRunner`` (implements ``MutationTransport``)."""

    iteration: Optional[int] = None

    def __init__(self, session: AgentSession, *, timeout_s: float = 120.0) -> None:
        self._session = session
        self._timeout_s = timeout_s

    async def generate_with_context(
        self,
        system_message: str,
        messages: List[Dict[str, str]],
        model: Any = None,
    ) -> str:
        session = self._session
        if session.mutation_backend is None:
            raise RuntimeError("mutation_backend is required for agent-host iteration")
        if session._selection is None:
            raise RuntimeError("parent must be selected before mutation")
        session.raise_if_aborted()

        prompt = {"system": system_message, "user": messages[0]["content"]}
        session._mutation_attempts = session._driver_mutation_attempts + 1
        session._driver_mutation_attempts = session._mutation_attempts

        layout, mutation, child_code = await asyncio.to_thread(
            _spawn_mutation,
            session,
            prompt=prompt,
            timeout_s=self._timeout_s,
            model=model,
        )
        session.raise_if_aborted()
        if not mutation.ok:
            raise MutationTransportFailure(mutation.error or "mutation backend failed")
        if child_code is None:
            raise MutationTransportFailure("no parseable code in mutation deliverable")
        spec = session._operator_spec
        if spec is None:
            raise RuntimeError("operator must be chosen before mutation")
        session._materialized_child = (child_code, "Full rewrite")
        if spec.parse_mode == "diff":
            return mutation.code
        lang = session.config.language
        return f"```{lang}\n{child_code}\n```"


def _spawn_mutation(
    session: AgentSession,
    *,
    prompt: dict[str, str],
    timeout_s: float = 120.0,
    model: Optional[str] = None,
) -> tuple[MutationLayout, MutationResult, Optional[str]]:
    """Shared layout + backend run + materialize (0173 Phase B/C)."""
    if session.mutation_backend is None:
        raise RuntimeError("mutation_backend is required for mutation spawn")
    if session._selection is None:
        raise RuntimeError("parent must be selected before mutation")
    parent = session._selection.parent
    spec = session._operator_spec
    if spec is None:
        raise RuntimeError("operator must be chosen before mutation")

    layout = mutation_layout(
        session.output_dir,
        session._iteration,
        session._mutation_attempts,
        file_suffix=session.config.file_suffix,
    )
    layout.work_dir.mkdir(parents=True, exist_ok=True)
    try:
        mutation = session.mutation_backend.run(
            MutationRequest(
                prompt=dict(prompt),
                parent_code=parent.code,
                work_dir=layout.work_dir,
                deliverable_path=layout.deliverable_path,
                timeout_s=timeout_s,
                model=model,
                retry_brief=session._retry_brief,
                layout=layout,
            )
        )
    except Exception as exc:
        raise MutationTransportFailure(f"mutation backend raised {exc!r}") from exc
    session._last_mutation_layout = layout
    session._last_mutation_trace = dict(mutation.backend_trace)
    if not mutation.ok:
        return layout, mutation, None
    child_code = materialize_child_code(
        mutation.code,
        parent.code,
        parse_mode=spec.parse_mode,
        language=session.config.language,
        diff_pattern=session.config.diff_pattern,
    )
    return layout, mutation, child_code
