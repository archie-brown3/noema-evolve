"""Thin MCP adapters for the agent host (task 0160).

No evolution logic here: serialize phase refusals and delegate to
``AgentSession`` methods. ``dispatch`` is the in-process tool surface a stdio
server binds to, and the same surface tests drive, so there is one code path
for "an outer agent called a tool".
"""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, Dict, Optional

from noema.agenthost.session import AgentSession, PhaseError, _REQUIRED_CALL


def _refuse_out_of_phase(handler):
    """A tool called out of order answers with the call the agent owes instead."""

    @functools.wraps(handler)
    async def wrapper(session: AgentSession, **arguments: Any) -> Dict[str, Any]:
        try:
            return await handler(session, **arguments)
        except PhaseError as err:
            return {
                "status": "refused",
                "required_call": err.required_call,
                "error": str(err),
                "attempted": err.attempted,
            }

    return wrapper


@_refuse_out_of_phase
async def begin_run(session: AgentSession) -> Dict[str, Any]:
    """MCP tool: seed the population with the evaluated initial program."""
    return await session.begin_run()


@_refuse_out_of_phase
async def next_target(session: AgentSession) -> Dict[str, Any]:
    """MCP tool: open the next target scope, or report the run complete."""
    return session.next_target()


@_refuse_out_of_phase
async def select_parent(session: AgentSession) -> Dict[str, Any]:
    """MCP tool: draw parent, inspirations, and this iteration's operator."""
    return session.select_parent()


@_refuse_out_of_phase
async def get_brief(session: AgentSession) -> Dict[str, Any]:
    """MCP tool: fire coordination advice and assemble the mutation prompt."""
    return await session.get_brief()


@_refuse_out_of_phase
async def run_mutation(
    session: AgentSession,
    *,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """MCP tool: spawn headless mutation, evaluate, and update the population.

    Caller must already have driven the session to ``briefed`` (``get_brief``).
    """
    if timeout_s is None:
        return await session.run_mutation()
    return await session.run_mutation(timeout_s=timeout_s)


@_refuse_out_of_phase
async def run_status(session: AgentSession) -> Dict[str, Any]:
    """MCP tool: read-only counters and stop state."""
    return session.run_status()


async def run_until_budget(session: AgentSession) -> Dict[str, Any]:
    """MCP tool: run the full burst until stop_children (Run 3 coarse path)."""
    if session._phase == "complete":
        return session.run_status()
    if session._phase not in ("idle", "open"):
        required = _REQUIRED_CALL[session._phase]
        return {
            "status": "refused",
            "required_call": required,
            "attempted": "run_until_budget",
            "error": (
                f"run_until_budget is not available yet: call {required} first"
                if required
                else "run_until_budget is not available: the run is complete"
            ),
        }
    return await session.run_agent_mode()


HANDLERS: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {
    "begin_run": begin_run,
    "next_target": next_target,
    "select_parent": select_parent,
    "get_brief": get_brief,
    "run_mutation": run_mutation,
    "run_status": run_status,
    "run_until_budget": run_until_budget,
}


async def dispatch(session: AgentSession, tool: str, **arguments: Any) -> Dict[str, Any]:
    """Call one agent-host tool by name; out-of-order calls answer ``refused``."""
    handler = HANDLERS.get(tool)
    if handler is None:
        raise KeyError(f"unknown agent-host tool: {tool}")
    return await handler(session, **arguments)


_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

# Tool metadata for stdio server registration. Phase order is part of the
# contract: a call out of order returns refused with the call the agent owes.
TOOLS = [
    {
        "name": "begin_run",
        "description": (
            "Evaluate the initial program and seed the population. Call once, "
            "before anything else. Returns children_accepted and stop_children."
        ),
        "parameters": _NO_ARGS,
    },
    {
        "name": "next_target",
        "description": (
            "Open the next target scope for a child. Call after begin_run or "
            "after a child is accepted. Returns status open with the iteration "
            "and target_scope, or status complete when the run has finished."
        ),
        "parameters": _NO_ARGS,
    },
    {
        "name": "select_parent",
        "description": (
            "Draw the parent program, its inspirations, and this iteration's "
            "mutation operator using the substrate and coordination policies. "
            "Requires next_target first. Selection is host-owned: the parent "
            "cannot be chosen or overridden by the caller."
        ),
        "parameters": _NO_ARGS,
    },
    {
        "name": "get_brief",
        "description": (
            "Fire the coordination advice hook and assemble the mutation prompt "
            "for the drawn parent. Requires select_parent first. Returns the "
            "system/user prompt, the brief text, and the operator name."
        ),
        "parameters": _NO_ARGS,
    },
    {
        "name": "run_mutation",
        "description": (
            "Start a headless coding session with the current mutation brief, wait "
            "for a complete child program on the deliverable path, evaluate it, and "
            "update the population. Requires get_brief first (phase briefed). "
            "Returns accepted, rejected (with retry_brief), mutation_failed, or refused."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timeout_s": {
                    "type": "number",
                    "description": "Optional wall-clock timeout for the mutation CLI.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "run_status",
        "description": (
            "Read-only run counters: children_accepted, stop_children, stopped, "
            "generation, and tokens_spent. Safe to call in any phase."
        ),
        "parameters": _NO_ARGS,
    },
    {
        "name": "run_until_budget",
        "description": (
            "Run 3 coarse path: execute the full burst until stop_children. "
            "Call once from idle; returns final run_status. Coordination runs "
            "inside the host. Refused if a per-child iteration is in progress."
        ),
        "parameters": _NO_ARGS,
    },
]

RUN_MUTATION_TOOL = next(tool for tool in TOOLS if tool["name"] == "run_mutation")
