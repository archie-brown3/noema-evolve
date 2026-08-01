"""Inner-session MCP server for deep CLI spawns (task 0179).

Read tools are thin wrappers over the host snapshot written at spawn time
(``tools/snapshot.json``, the 0175 file contract kept as backup). The population
is frozen for the duration of one deep session, so that snapshot *is* the live
view. Submit tools finalize the host-owned deliverable and tell the agent to
stop; the host still owns parse -> evaluate -> store after the session ends.

List-shaped results are returned as a single JSON object
(``{"programs": [...], "count": n}``) so FastMCP emits one content block rather
than one block per element (or zero blocks for an empty list).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from noema.agenthost.read_tools import build_snapshot

TOOLS_DIRNAME = "tools"
SNAPSHOT_NAME = "snapshot.json"
MCP_CONFIG_NAME = "mcp.json"
SERVER_NAME = "noema"

INSTRUCTIONS = (
    "Noema evolution memory for this session. Read tools query the population "
    "and coordination state. When the deliverable is ready call the submit tool "
    "once and then stop — the host evaluates and stores it."
)


def prepare_inner_mcp(
    work_dir: Union[str, Path],
    session,
    *,
    mode: str,
    deliverable: Union[str, Path],
) -> Path:
    """Write ``tools/snapshot.json`` + ``tools/mcp.json`` for one deep CLI spawn."""
    tools = Path(work_dir) / TOOLS_DIRNAME
    tools.mkdir(parents=True, exist_ok=True)
    snapshot_path = tools / SNAPSHOT_NAME
    with snapshot_path.open("w") as handle:
        json.dump(build_snapshot(session), handle)

    config_path = tools / MCP_CONFIG_NAME
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    SERVER_NAME: {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [
                            "-m",
                            "noema.agenthost.inner_session_mcp",
                            "--snapshot",
                            str(snapshot_path.resolve()),
                            "--mode",
                            mode,
                            "--deliverable",
                            str(Path(deliverable).resolve()),
                        ],
                    }
                }
            },
            indent=2,
        )
    )
    return config_path


def _programs(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return snapshot.get("programs") or {}


def _lineage(snapshot: Dict[str, Any]) -> Dict[str, Optional[str]]:
    return snapshot.get("lineage") or {}


def _program_list(programs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"programs": programs, "count": len(programs)}


def get_memory_status(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    population = snapshot.get("population") or {}
    best = population.get("best_program")
    return {
        "num_programs": len(_programs(snapshot)),
        "best_fitness": best.get("fitness") if best else None,
        "regions": population.get("regions") or [],
        "topology": population.get("topology"),
        "run_status": snapshot.get("run_status") or {},
    }


def get_best_programs(snapshot: Dict[str, Any], limit: int = 5) -> Dict[str, Any]:
    top = list((snapshot.get("population") or {}).get("top_programs") or [])
    return _program_list(top[: max(0, limit)])


def get_program(snapshot: Dict[str, Any], program_id: str) -> Dict[str, Any]:
    program = _programs(snapshot).get(program_id)
    if program is None:
        return {"error": f"unknown program id: {program_id}"}
    return program


def get_parents_by_child(snapshot: Dict[str, Any], child_id: str, limit: int = 3) -> Dict[str, Any]:
    programs = _programs(snapshot)
    lineage = _lineage(snapshot)
    chain: List[Dict[str, Any]] = []
    current_id = child_id
    seen = {child_id}
    while len(chain) < max(0, limit):
        parent_id = lineage.get(current_id)
        if parent_id is None or parent_id in seen:
            break
        parent = programs.get(parent_id)
        if parent is None:
            break
        chain.append(parent)
        seen.add(parent_id)
        current_id = parent_id
    return _program_list(chain)


def get_children_by_parent(
    snapshot: Dict[str, Any], parent_id: str, limit: int = 10
) -> Dict[str, Any]:
    programs = _programs(snapshot)
    lineage = _lineage(snapshot)
    children = [
        programs[pid]
        for pid, pid_parent in lineage.items()
        if pid_parent == parent_id and pid in programs
    ]
    children.sort(key=lambda item: item.get("fitness") or 0.0, reverse=True)
    return _program_list(children[: max(0, limit)])


def get_coordination_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return snapshot.get("coordination") or {}


# ponytail: stop is prompt-level — the tool response tells the agent to stop. If
# live runs show agents idling after submit, poll the deliverable in CliRunner
# and terminate the subprocess.
_STOP = "Deliverable written. Stop now — the host takes over from here."


def submit_mutation(deliverable: Union[str, Path], code: str) -> Dict[str, Any]:
    """Finalize ``child.py``; the host parses, evaluates, and admits it."""
    if not code or not code.strip():
        return {"error": "code is empty; submit the complete child program"}
    path = Path(deliverable)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    return {"status": "submitted", "path": str(path), "next": _STOP}


def submit_coordination(deliverable: Union[str, Path], response: str) -> Dict[str, Any]:
    """Finalize the LLM response text consumed by the coordination module."""
    if not isinstance(response, str) or not response.strip():
        return {"error": "response must be non-empty text"}
    path = Path(deliverable)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"response": response}))
    return {"status": "submitted", "path": str(path), "next": _STOP}


def build_server(snapshot_path: Union[str, Path], mode: str, deliverable: Union[str, Path]):
    from mcp.server.fastmcp import FastMCP

    snapshot = json.loads(Path(snapshot_path).read_text())
    deliverable_path = Path(deliverable)
    mcp = FastMCP("Noema inner session", instructions=INSTRUCTIONS, json_response=True)

    @mcp.tool(name="get_memory_status")
    def _memory_status() -> dict:
        """Population counts, best fitness, regions, and run status."""
        return get_memory_status(snapshot)

    @mcp.tool(name="get_best_programs")
    def _best_programs(limit: int = 5) -> dict:
        """Top programs by fitness."""
        return get_best_programs(snapshot, limit)

    @mcp.tool(name="get_program")
    def _program(program_id: str) -> dict:
        """One retained program by id: code, fitness, metrics, and metadata."""
        return get_program(snapshot, program_id)

    @mcp.tool(name="get_parents_by_child")
    def _parents_by_child(child_id: str, limit: int = 3) -> dict:
        """Ancestors of a program, nearest first."""
        return get_parents_by_child(snapshot, child_id, limit)

    @mcp.tool(name="get_children_by_parent")
    def _children_by_parent(parent_id: str, limit: int = 10) -> dict:
        """Programs derived from a parent, best first."""
        return get_children_by_parent(snapshot, parent_id, limit)

    @mcp.tool(name="get_coordination_snapshot")
    def _coordination_snapshot() -> dict:
        """Current coordination-mechanism state."""
        return get_coordination_snapshot(snapshot)

    if mode == "mutation":

        @mcp.tool(name="submit_mutation")
        def _submit_mutation(code: str) -> dict:
            """Submit the complete improved program, then stop."""
            return submit_mutation(deliverable_path, code)

    else:

        @mcp.tool(name="submit_coordination")
        def _submit_coordination(response: str) -> dict:
            """Submit the coordination LLM's completion text, then stop."""
            return submit_coordination(deliverable_path, response)

    return mcp


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Noema inner-session MCP server")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--mode", required=True, choices=("mutation", "coordination"))
    parser.add_argument("--deliverable", required=True)
    args = parser.parse_args(argv)
    build_server(args.snapshot, args.mode, args.deliverable).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
