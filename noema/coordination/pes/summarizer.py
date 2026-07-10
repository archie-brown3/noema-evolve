"""
Summary phase of the PES arm (LoongFlow: agents/general_agent/summary.py).

Extracted from module.py (task 0060, behavior-identical split). Assessment is
pure Python (LoongFlow's _assess); only the causal reflection is an LLM call
(LoongFlow's _reflect), drained deferred at the generation tick — see the
module docstring's deviation #4.
"""

import logging

logger = logging.getLogger(__name__)

# =============================================================================
# BORROWED CODE — reflection prompt adapted from LoongFlow (Apache-2.0)
# Source: src/loongflow/framework/claude_code/general_prompt.py
#         (GENERAL_SUMMARY_SYSTEM lines 341-364, GENERAL_SUMMARY_USER lines
#         373-448; local clone /home/archie/LoongFlow)
# Condensed for a single-call recast (LoongFlow's _reflect is one agent.run
# call, summary.py:325); the "causal over correlational" instruction is kept
# verbatim-ish as the load-bearing line. Local changes marked NOEMA.
# =============================================================================

REFLECTION_SYSTEM = """You are a reflective analyst in a structured problem-solving system.
A plan was proposed and executed as a code mutation. Given the plan, the parent
solution it started from, the resulting child solution, and the measured outcome,
explain WHY the outcome happened.

Key principles:
- Causal over correlational: explain why the change worked or failed, not just
  that the score moved.
- Be concrete and brief: 2-4 sentences the next attempt can act on.
- On failure, name the specific cause (e.g. the reported error) and what to avoid."""
# NOEMA: condensed from GENERAL_SUMMARY_SYSTEM; the Assessment/What-Worked/
# What-Didn't/Insights/Recommendations section skeleton is dropped in favour of
# a short free-text explanation (single-call recast, prompt-suffix consumer).

REFLECTION_USER_TEMPLATE = """# Outcome to Explain
The plan below was executed as one mutation. Outcome: **{outcome}** \
(fitness {parent_fitness:.4f} -> {child_fitness:.4f}).{error_block}

# Plan That Was Executed
{plan}

# Parent Solution (fitness {parent_fitness:.4f})
```
{parent_code}
```

# Resulting Child Solution (fitness {child_fitness:.4f})
```
{child_code}
```

# Your Task
In 2-4 sentences, explain the CAUSE of this outcome and one concrete lesson for
the next mutation of this lineage. Output only the explanation."""

# ============================== END BORROWED =================================

# Outcome labels mirror LoongFlow's Assessment enum (summary.py:233-247)
IMPROVED = "improved"
REGRESSED = "regressed"
STALE = "stale"
FAILED = "failed"
