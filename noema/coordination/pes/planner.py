"""
Plan phase of the PES arm (LoongFlow: agents/general_agent/planner.py).

Extracted from module.py (task 0060, behavior-identical split). The
PESPlannerModule façade owns all shared state (_plans, the reflection queue,
config knobs, llm) and hands itself to the phase object by reference.
"""

import logging

logger = logging.getLogger(__name__)

# =============================================================================
# BORROWED CODE — prompt text adapted from LoongFlow (Apache-2.0)
# Source: https://github.com/baidu-baige/LoongFlow
#         src/loongflow/framework/claude_code/general_prompt.py
#         (GENERAL_PLANNER_SYSTEM lines 27-79, GENERAL_PLANNER_USER lines
#         82-183; local clone /home/archie/LoongFlow)
# Condensed for a single-call recast; structural skeleton (Situation Analysis /
# Strategy / Action Steps / Success Criteria) kept verbatim from the mandated
# plan structure. Local changes marked NOEMA.
# =============================================================================

PLANNER_SYSTEM = """You are a strategic planner in a structured problem-solving system.
Design a clear, actionable plan that guides the next code mutation to improve
from the current solution (parent) to a better solution (child).

Key principles:
- Be specific: vague plans lead to vague results. State exactly what should be done.
- Be actionable: the implementer must understand precisely what steps to take.
- Learn from history: avoid repeating approaches that already failed.
- Stay focused: every plan element should directly serve the objective."""
# NOEMA: condensed from GENERAL_PLANNER_SYSTEM; PES-cycle framing and
# tool/skill instructions dropped (no tools in a single-call recast)

PLANNER_USER_TEMPLATE = """# Task Objective
Improve the program's fitness score through one targeted mutation.

# Current Solution (parent)
- Fitness: {fitness:.4f}
- Metrics: {metrics}

```
{code}
```

# Prior Plan For This Solution
{prior_block}
{recent_block}
# Population Status
- Recent best-fitness history: {best_history}
- Recent average-fitness history: {avg_history}

# Your Mission
Design a plan for the next mutation of this program.

If improving on a prior plan: identify what worked and what didn't based on its
outcome, design targeted improvements, and avoid repeating approaches that
already failed. If no prior plan exists, design a strategy from scratch.

Your plan MUST follow this exact structure (keep each section to 1-3 short
bullets; output ONLY the plan):

# Plan

## Situation Analysis
[current state: core problem, what the prior plan's outcome tells us, risks]

## Strategy
[chosen approach and why it suits this program]

## Action Steps
[numbered, specific steps the mutation should take]

## Success Criteria
[what metrics or evidence indicate the mutation succeeded]"""
# NOEMA: condensed from GENERAL_PLANNER_USER; solution-pack/manifest and
# workspace/skills sections dropped (single-file programs, no tools);
# "Expected Deliverables" section dropped (deliverable fixed by substrate);
# brevity constraint added (plan is a prompt suffix, not a standalone file);
# {recent_block} is a noema-original field (deviation #6) — no LoongFlow analog.

# ============================== END BORROWED =================================

_HISTORY_TAIL = 5  # recent history entries shown to the planner
