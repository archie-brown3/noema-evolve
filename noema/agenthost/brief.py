"""The brief: this host's entire prompt surface.

The controller assembles an OpenEvolve prompt and appends the arm's advice to it.
Here the agent harness owns its own context, so the host hands over a single
document instead: the task, the parent it must improve, and the arm's guidance
under the same `COORDINATION_HEADER` delimiter the controller uses — so a
coordination-OFF brief carries no block and no header, exactly as in a
controller run.

Section structure follows LoongFlow's executor prompt (`general_prompt.py`): an
explicit objective, the prior solution with its score, and a stated output
contract, so the agent never has to guess what "done" means.
"""

from typing import Any, Mapping, Optional

from noema.evolution.prompts import COORDINATION_HEADER

_OUTPUT_CONTRACT = """\
# Output Contract
Submit the complete replacement program through `submit_child`. The host
evaluates what you submit; your own test runs are advisory only."""


def render_brief(
    *,
    task: str,
    parent_code: str,
    parent_metrics: Mapping[str, Any],
    coordination_block: str = "",
    operator_instruction: Optional[str] = None,
) -> str:
    metrics = ", ".join(
        f"{name}={_format_metric(value)}" for name, value in sorted(parent_metrics.items())
    )
    sections = [
        f"# Task Objective\n{task}",
        f"# Parent Program\nScore: {metrics or 'none'}\n\n```\n{parent_code}\n```",
    ]
    if operator_instruction:
        sections.append(f"# Operator\n{operator_instruction}")
    sections.append(_OUTPUT_CONTRACT)
    brief = "\n\n".join(sections)
    if coordination_block:
        brief = brief + COORDINATION_HEADER + coordination_block
    return brief


def _format_metric(value: Any) -> str:
    return f"{value:.4g}" if isinstance(value, (int, float)) else str(value)
