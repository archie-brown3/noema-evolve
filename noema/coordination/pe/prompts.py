"""Prompt builders for Punctuated Equilibrium (task 0109).

Adapted from LEVI (https://github.com/ttanv/levi, MIT (c) 2025 Temoor Tanveer),
``levi/equilibrium/prompts.py`` — reworded for noema's single-program benchmarks
(the donor targets multi-component "bundle" artifacts).
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Tuple

_EVOLVE_BLOCK_RE = re.compile(
    r"# EVOLVE-BLOCK-START\n(.*?)\n# EVOLVE-BLOCK-END",
    re.DOTALL,
)


def extract_evolve_block(code: str) -> Optional[str]:
    """Return the content between EVOLVE-BLOCK markers, or None if not found."""
    m = _EVOLVE_BLOCK_RE.search(code)
    return m.group(1).strip() if m else None


def splice_evolve_block(scaffold: str, new_block: str) -> Optional[str]:
    """Replace the EVOLVE-BLOCK content in scaffold with new_block."""
    if not _EVOLVE_BLOCK_RE.search(scaffold):
        return None
    return _EVOLVE_BLOCK_RE.sub(
        f"# EVOLVE-BLOCK-START\n{new_block}\n# EVOLVE-BLOCK-END",
        scaffold,
    )


# Adaptive paradigm shift prompts keyed by budget stage.
# Early: explore radically different approaches.
# Mid: synthesize and recombine strengths from existing solutions.
# Late: targeted refinement of weak spots while preserving what works.

PARADIGM_SHIFT_PROMPTS = {
    "early": """# Algorithmic Paradigm Shift Challenge

## Problem
{problem_description}

## Function Signature
```python
{function_signature}
```

## Current Best Solutions (From Different Behavioral Regions)

The archive has evolved through {n_evaluations} evaluations across {n_regions} behavioral regions.
Below are the best-performing solutions from each region:

{representative_solutions}

## Your Challenge: PARADIGM SHIFT

Analyze the representative solutions above and identify their core algorithmic paradigms.

Your goal is to engineer a **fundamentally different algorithmic approach** that explores untapped regions of the solution space.

### Analysis Steps:
1. **Identify current paradigms**: What algorithmic strategies do the existing solutions use? (e.g., greedy, graph-based, dynamic programming, heuristic search, brute-force with pruning, etc.)
2. **Find the gap**: What paradigms are NOT represented in the current solutions?
3. **Design a novel approach**: Synthesize a solution using a completely different conceptual framework and data structure strategy than those found in the examples

### Instructions:
1. Study the function signature carefully - match it EXACTLY
2. Actively avoid the core logic, heuristics, and search patterns used in the existing solutions
3. Design a solution using a COMPLETELY DIFFERENT strategy

### Critical Requirements:
- Your function signature MUST match exactly: `{function_signature}`
- Use only standard Python libraries (numpy, collections, itertools, math, heapq, functools, etc.) and torch if needed
- The code must be syntactically valid and complete
- Include ALL necessary imports at the top
- Do NOT use placeholders, ellipses (...), or incomplete code
- Ensure the solution handles all edge cases

## Output
Output ONLY complete, runnable Python code in a ```python block. No explanations before or after.
""",
    "mid": """# Solution Synthesis Challenge

## Problem
{problem_description}

## Function Signature
```python
{function_signature}
```

## Current Best Solutions (From Different Behavioral Regions)

The archive has evolved through {n_evaluations} evaluations across {n_regions} behavioral regions.
Below are the best-performing solutions from each region:

{representative_solutions}

## Your Challenge: SYNTHESIZE A STRONGER SOLUTION

The archive has been evolving and found several decent approaches. Your goal is to **combine the best ideas** from the existing solutions into a stronger hybrid.

### Analysis Steps:
1. **Identify strengths**: What does each solution do well? What cases does each handle effectively?
2. **Identify weaknesses**: Where does each solution fall short? What edge cases or scenarios cause poor performance?
3. **Synthesize**: Build a new solution that combines the strongest elements from multiple approaches while addressing their individual weaknesses

### Instructions:
1. Study the function signature carefully - match it EXACTLY
2. Borrow and adapt the best techniques from the existing solutions
3. Address weaknesses you observe in the current approaches
4. The result should meaningfully improve on the existing solutions, not just copy one of them

### Critical Requirements:
- Your function signature MUST match exactly: `{function_signature}`
- Use only standard Python libraries (numpy, collections, itertools, math, heapq, functools, etc.) and torch if needed
- The code must be syntactically valid and complete
- Include ALL necessary imports at the top
- Do NOT use placeholders, ellipses (...), or incomplete code
- Ensure the solution handles all edge cases

## Output
Output ONLY complete, runnable Python code in a ```python block. No explanations before or after.
""",
    "late": """# Targeted Refinement Challenge

## Problem
{problem_description}

## Function Signature
```python
{function_signature}
```

## Current Best Solutions (From Different Behavioral Regions)

The archive has evolved through {n_evaluations} evaluations across {n_regions} behavioral regions.
Below are the best-performing solutions from each region:

{representative_solutions}

## Your Challenge: TARGETED IMPROVEMENT

The archive is mature. The solutions above represent well-evolved approaches. Your goal is to make a **focused, high-impact improvement** to the best-performing approach.

### Analysis Steps:
1. **Study the best solution carefully**: Understand exactly what it does and why
2. **Find the weak spot**: What specific scenarios, edge cases, or parameter ranges cause the best solution to lose points?
3. **Make a surgical fix**: Improve the handling of those weak cases without degrading performance on cases that already work well

### Instructions:
1. Study the function signature carefully - match it EXACTLY
2. Start from the logic of the highest-scoring solution
3. Make targeted changes to address its specific weaknesses
4. Preserve the core strengths — do NOT rewrite from scratch

### Critical Requirements:
- Your function signature MUST match exactly: `{function_signature}`
- Use only standard Python libraries (numpy, collections, itertools, math, heapq, functools, etc.) and torch if needed
- The code must be syntactically valid and complete
- Include ALL necessary imports at the top
- Do NOT use placeholders, ellipses (...), or incomplete code
- Ensure the solution handles all edge cases

## Output
Output ONLY complete, runnable Python code in a ```python block. No explanations before or after.
""",
}

# Budget stage thresholds
EARLY_THRESHOLD = 0.3
LATE_THRESHOLD = 0.6


def get_budget_stage(budget_progress: float) -> str:
    """Map budget progress (0-1) to a stage name.

    Always returns 'early' to use the large paradigm shift prompt
    (radical exploration) regardless of budget progress.
    """
    return "early"


# Default prompt (backwards compat) — same as early stage
PARADIGM_SHIFT_PROMPT = PARADIGM_SHIFT_PROMPTS["early"]


VARIANT_GENERATION_PROMPT = """# Generate Variant of Paradigm Shift Solution

## Problem
{problem_description}

## Function Signature
```python
{function_signature}
```

## Base Paradigm Shift Solution (Score: {base_score:.17g})
```python
{base_code}
```

## Your Task
Generate a VARIANT of the above paradigm shift solution by:
1. Keeping the core algorithmic approach intact
2. Making targeted modifications to:
   - Constants and thresholds
   - Secondary heuristics
   - Edge case handling
   - Implementation details

The variant should explore nearby regions of the solution space while preserving the novel approach.

## Output
Output ONLY the complete Python code in a ```python block.
"""


def extract_signature_from_code(code: str) -> str:
    """Find the line starting with 'def ' inside the code."""
    for line in code.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("def ") and "(" in line_stripped:
            if ":" in line_stripped:
                return line_stripped.split(":")[0] + ":"
            return line_stripped
    return "def f():"


def get_function_signature(scaffold: str, representative_code: str) -> str:
    """Get function signature from the representative code or the scaffold."""
    sig = extract_signature_from_code(representative_code)
    if sig != "def f():":
        return sig
    return extract_signature_from_code(scaffold)


def paradigm_shift_prompt(
    domain_context: str,
    representatives: Sequence[Tuple[str, float]],
    n_evaluations: int,
    scaffold: str,
    budget_progress: float = 0.0,
) -> str:
    first_rep_code = representatives[0][0] if representatives else ""
    function_signature = get_function_signature(scaffold, first_rep_code)

    rep_text_parts = []
    for idx, (code, score) in enumerate(representatives):
        rep_text_parts.append(
            f"### Region {idx + 1} (Score: {score:.17g})\n```python\n{code}\n```"
        )
    representative_solutions = "\n\n".join(rep_text_parts)

    stage = get_budget_stage(budget_progress)
    template = PARADIGM_SHIFT_PROMPTS[stage]

    # Adjust the Output instruction based on whether the evolve block defines the function
    # or is inside the function.
    is_function_definition = first_rep_code.strip().startswith("def ")
    if not is_function_definition:
        tailored_output = (
            "## Output\n"
            "Return ONLY the inner code block that goes between # EVOLVE-BLOCK-START and "
            "# EVOLVE-BLOCK-END (do not redefine the function or return the signature) in a "
            "single ```python code block. No explanations before or after."
        )
        template = template.split("## Output")[0] + tailored_output

    return template.format(
        problem_description=domain_context,
        function_signature=function_signature,
        n_evaluations=n_evaluations,
        n_regions=len(representatives),
        representative_solutions=representative_solutions,
    )


def variant_prompt(
    domain_context: str,
    base_code: str,
    base_score: float,
    scaffold: str,
) -> str:
    function_signature = get_function_signature(scaffold, base_code)

    template = VARIANT_GENERATION_PROMPT
    is_function_definition = base_code.strip().startswith("def ")
    if not is_function_definition:
        tailored_output = (
            "## Output\n"
            "Return ONLY the inner code block that goes between # EVOLVE-BLOCK-START and "
            "# EVOLVE-BLOCK-END (do not redefine the function or return the signature) in a "
            "single ```python code block. No explanations before or after."
        )
        template = template.split("## Output")[0] + tailored_output

    return template.format(
        problem_description=domain_context,
        function_signature=function_signature,
        base_code=base_code,
        base_score=base_score,
    )
