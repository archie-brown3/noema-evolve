"""Agent-host mutation prompts for headless coding CLIs.

Controller mutations speak SEARCH/REPLACE to an LLM; CLI mutations write a
complete deliverable file. This module adapts the shared openevolve prompt
scaffold for that contract without changing per-example system messages or the
global controller prompt path.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from openevolve.utils.metrics_utils import get_fitness_score  # type: ignore[import-untyped]

from noema.evolution.prompts import COORDINATION_HEADER

_TASK_HEADER = "# Task\n"
_EVOLUTION_HISTORY_HEADER = "# Program Evolution History"
_CURRENT_PROGRAM_HEADER = "# Current Program"
_TECHNICAL_REQUIREMENTS_RE = re.compile(
    r"\n\s*TECHNICAL REQUIREMENTS(?:\s*&\s*BEST PRACTICES)?\s*:.*\Z",
    re.DOTALL | re.IGNORECASE,
)
_TOP_PROGRAMS_SECTION_RE = re.compile(
    r"## Top Performing Programs\n.*?(?=\n## |\n# Current Program|\n# Second Parent|\n# Task|\Z)",
    re.DOTALL,
)
_INSPIRATIONS_SECTION_RE = re.compile(
    r"## Inspiration Programs\n.*?(?=\n## |\n# Current Program|\n# Second Parent|\n# Task|\Z)",
    re.DOTALL,
)
_SEARCH_REPLACE_MARKERS = (
    "<<<<<<< SEARCH",
    ">>>>>>> REPLACE",
)

CLI_MUTATION_SYSTEM_PREFIX = """HOST SESSION ROLE:
This is one iteration in a larger outer search — write one improved program and submit it. The host explores variants across iterations; you are not the optimizer.

Hard rules for this session:
- Keep the Current Program's public entry-point exactly (same function name, parameters, and return type/shape). Only change the body.
- Ship a direct constructor: the function should immediately return its result (fixed values, a closed-form recipe, or a deterministic construction). No hill climbing, local search, gradient/scipy optimize, simulated annealing, differential evolution, random restarts, or other nested search — not in the submitted program and not as work you do before submitting.
- Do not create auxiliary files (``dev_opt.py``, ``search.py``, tuning scripts). Use Noema MCP read tools for context, then call ``submit_mutation`` with the complete program — that is the only way to ship.

"""

CLI_MUTATION_TASK = """# Task
This is one iteration of the outer search loop. Rewrite the Current Program's body and submit it.

Session tools:
- Use the Noema inner-session MCP tools (``get_memory_status``, ``get_best_programs``, ``get_program``, etc.) for population and run context. Do not explore via shell, file writes outside ``submit_mutation``, repo reads, or ad-hoc scripts — the MCP snapshot is the authoritative read surface.
- Population peers are listed by id under Population catalog (and via ``get_best_programs``). Call ``get_program`` with an id to load full source — list tools do not return code.
- When the rewritten program is ready, call ``submit_mutation`` once with the full program. That writes the deliverable and ends this session.

Requirements:
- Preserve the Current Program's entry-point signature exactly (name, args, return contract). Change only the implementation body.
- Direct construction only: return concrete outputs (for example literal coordinates or a closed-form layout). Do not embed or run hill climbing, iterative refinement, multi-restart metaheuristics, simulated annealing, or any offline optimizer. Reason about a construction, then submit — the outer loop is already the search.
- Ship within the session time budget. A mediocre or unvalidated score is fine; an unchanged deliverable is not.

You must call ``submit_mutation`` when done. Do not idle after submitting."""


def uses_cli_mutation(host: Any) -> bool:
    """True when this host drives mutation through a headless coding CLI."""
    via = getattr(host, "mutation_via", None)
    return isinstance(via, str) and via.startswith("cli/")


def program_fitness(program: Any) -> float:
    """Best-effort fitness for catalog rows (Program, ProgramView, or dict)."""
    fitness = getattr(program, "fitness", None)
    if isinstance(fitness, (int, float)):
        return float(fitness)
    if isinstance(program, dict):
        raw = program.get("fitness")
        if isinstance(raw, (int, float)):
            return float(raw)
        metrics = program.get("metrics") or {}
    else:
        metrics = getattr(program, "metrics", None) or {}
    if isinstance(metrics, dict) and metrics:
        return float(get_fitness_score(metrics, []))
    return 0.0


def program_id(program: Any) -> str:
    pid = getattr(program, "id", None)
    if pid is None and isinstance(program, dict):
        pid = program.get("id")
    return str(pid) if pid is not None else "unknown"


def format_cli_population_catalog(programs: Sequence[Any]) -> str:
    """Id + fitness catalog; source is loaded via ``get_program``."""
    lines = [
        "## Population catalog",
        "Peers by id (fitness). Call ``get_program`` with an id to load full source; "
        "``get_best_programs`` returns the same catalog without code.",
        "",
    ]
    seen: set[str] = set()
    rows: List[tuple[str, float]] = []
    for program in programs:
        pid = program_id(program)
        if pid in seen:
            continue
        seen.add(pid)
        rows.append((pid, program_fitness(program)))
    if not rows:
        lines.append("- (empty — only the Current Program below is available)")
    else:
        for pid, fitness in rows:
            lines.append(f"- `{pid}` — fitness {fitness:.4f}")
    return "\n".join(lines) + "\n"


def merge_catalog_programs(*groups: Optional[Sequence[Any]]) -> List[Any]:
    """Deduplicate programs by id, preserving first-seen order."""
    merged: List[Any] = []
    seen: set[str] = set()
    for group in groups:
        if not group:
            continue
        for program in group:
            pid = program_id(program)
            if pid in seen:
                continue
            seen.add(pid)
            merged.append(program)
    return merged


def adapt_prompt_user_for_cli_mutation(
    user: str,
    *,
    catalog_programs: Optional[Sequence[Any]] = None,
) -> str:
    """Slim history dumps and replace the controller task with the CLI contract."""
    if COORDINATION_HEADER in user:
        prefix, suffix = user.split(COORDINATION_HEADER, 1)
        prefix = _adapt_user_body(prefix, catalog_programs=catalog_programs)
        return prefix + COORDINATION_HEADER + suffix
    return _adapt_user_body(user, catalog_programs=catalog_programs)


def adapt_prompt_system_for_cli_mutation(system: str) -> str:
    """Prepend host session role and drop example boilerplate unhelpful for CLI sessions."""
    body = _strip_technical_requirements(system.strip())
    if not body:
        return CLI_MUTATION_SYSTEM_PREFIX.strip() + "\n"
    return CLI_MUTATION_SYSTEM_PREFIX + body + "\n"


def adapt_prompt_for_cli_mutation(
    prompt: Dict[str, str],
    *,
    catalog_programs: Optional[Sequence[Any]] = None,
) -> Dict[str, str]:
    """Return a copy of ``prompt`` with system/user halves adapted for CLI mutation."""
    adapted = dict(prompt)
    adapted["system"] = adapt_prompt_system_for_cli_mutation(prompt.get("system", ""))
    adapted["user"] = adapt_prompt_user_for_cli_mutation(
        prompt.get("user", ""),
        catalog_programs=catalog_programs,
    )
    return adapted


def prompt_uses_search_replace(user: str) -> bool:
    return any(marker in user for marker in _SEARCH_REPLACE_MARKERS)


def _adapt_user_body(
    user: str,
    *,
    catalog_programs: Optional[Sequence[Any]],
) -> str:
    slimmed = _replace_code_dumps_with_catalog(user, catalog_programs=catalog_programs)
    return _replace_task_section(slimmed)


def _replace_code_dumps_with_catalog(
    user: str,
    *,
    catalog_programs: Optional[Sequence[Any]],
) -> str:
    catalog = format_cli_population_catalog(catalog_programs or ())
    if _TOP_PROGRAMS_SECTION_RE.search(user):
        user = _TOP_PROGRAMS_SECTION_RE.sub(catalog.rstrip() + "\n\n", user, count=1)
    elif _EVOLUTION_HISTORY_HEADER in user and _CURRENT_PROGRAM_HEADER in user:
        head, _, rest = user.partition(_EVOLUTION_HISTORY_HEADER)
        _, _, tail = rest.partition(_CURRENT_PROGRAM_HEADER)
        user = (
            head.rstrip()
            + "\n\n"
            + _EVOLUTION_HISTORY_HEADER
            + "\n"
            + catalog
            + "\n"
            + _CURRENT_PROGRAM_HEADER
            + tail
        )
    user = _INSPIRATIONS_SECTION_RE.sub("", user)
    return user


def _strip_technical_requirements(system: str) -> str:
    """Remove per-example TECHNICAL REQUIREMENTS blocks from CLI system prompts."""
    return _TECHNICAL_REQUIREMENTS_RE.sub("", system).rstrip()


def _replace_task_section(user: str) -> str:
    if _TASK_HEADER in user:
        head, _ = user.split(_TASK_HEADER, 1)
        return head.rstrip() + "\n\n" + CLI_MUTATION_TASK.strip() + "\n"
    return user.rstrip() + "\n\n" + CLI_MUTATION_TASK.strip() + "\n"
