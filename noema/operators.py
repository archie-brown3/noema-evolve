# ============================================================================
# BORROWED CODE — task-instruction paragraphs from EoH (Evolution of Heuristics)
# Source: https://github.com/FeiLiu36/EoH (MIT License), commit 36d10d4
#         eoh/src/eoh/eoh/evolution.py :: Evolution._build_prompt
# Citation: Fei Liu, Xialiang Tong, Mingxuan Yuan, Xi Lin, Fu Luo, Zhenkun Wang,
#           Zhichao Lu, Qingfu Zhang, "Evolution of Heuristics: Towards
#           Efficient Automatic Algorithm Design Using Large Language Model",
#           ICML 2024.
#
# This is a hybrid, not a byte-for-byte port: each operator's EoH task-
# instruction paragraph (the "please create/modify/simplify..." text) is
# spliced into noema's existing prompt scaffold (metrics/evolution-history/
# artifacts placeholders and the SEARCH/REPLACE diff-format instructions from
# openevolve/prompt/templates.py's DIFF_USER_TEMPLATE/FULL_REWRITE_USER_TEMPLATE)
# in place of the scaffold's own generic "# Task" section. Local additions are
# marked "# NOEMA:".
#
# Deviations from EoH's own design (recorded per house convention, see
# noema/coordination/hifo/module.py):
#   1. i1 (population-init, 0 parents) is excluded — EoH only fires it once at
#      population creation; noema's per-iteration mutation loop has no
#      equivalent moment, every iteration mutates an existing parent.
#   2. noema fires exactly ONE operator per iteration (uniform random draw,
#      see NoemaController._choose_operator) — EoH's own main loop instead
#      fires every operator every generation, each with its own static weight.
#      This is an explicit loop-shape deviation, not an oversight.
#   3. No weight/reward field on OperatorSpec. Reward-based/adaptive operator
#      selection is a separate, later effort (task 0018) — this menu is
#      static data, deliberately not scaffolded to anticipate that.
# ============================================================================

"""
The EoH-derived mutation operator menu: five prompt strategies (e1, e2, m1,
m2, m3) selectable in place of noema's default diff/full-rewrite toggle.
Strictly opt-in — see NoemaConfig.mutation_operators (None preserves today's
exact behavior byte-for-byte).

Every template below is a plain str eventually passed through
TemplateManager/PromptSampler's `.format(**kwargs)` — every `{name}` here is a
real substitution placeholder (metrics, evolution_history, current_program,
language, parent2_program), not literal text. There is no invented "wrap your
thought like {this}" example anywhere: EoH's own instruction is exactly
"The description must be inside a brace." (no literal brace shown to the
model), reproduced verbatim below.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    template_key: str
    parse_mode: str  # "diff" | "full_rewrite"
    arity: int  # 1 or 2
    has_thought: bool  # False only for m3 — EoH's own code-only exception


_DIFF_FORMAT_INSTRUCTIONS = """\
You MUST use the exact SEARCH/REPLACE diff format shown below to indicate changes:

<<<<<<< SEARCH
# Original code to find and replace (must match exactly)
=======
# New replacement code
>>>>>>> REPLACE

You can suggest multiple changes. Each SEARCH section must exactly match code in the current program.
Be thoughtful about your changes and explain your reasoning thoroughly.

IMPORTANT: Do not rewrite the entire program - focus on targeted improvements."""

_REWRITE_FORMAT_INSTRUCTIONS = """\
provide the complete new program code.

IMPORTANT: Make sure your rewritten program maintains the same inputs and outputs
as the original program, but with improved internal implementation.

```{language}
# Your rewritten program here
```"""

# NOEMA: noema's existing scaffold (metrics/history/artifacts/current-program
# placeholders), shared by every operator template — see DIFF_USER_TEMPLATE /
# FULL_REWRITE_USER_TEMPLATE in openevolve/prompt/templates.py.
_SCAFFOLD_HEADER = """\
# Current Program Information
- Current performance metrics: {metrics}
- Areas identified for improvement: {improvement_areas}

{artifacts}

# Program Evolution History
{evolution_history}

# Current Program
```{language}
{current_program}
```
"""

# NOEMA: second parent for arity-2 operators (e1/e2); not part of noema's
# existing scaffold, added here since only e1/e2 need it.
_PARENT2_BLOCK = """
# Second Parent Program
```{language}
{parent2_program}
```
"""

EOH_E1_USER_TEMPLATE = (
    _SCAFFOLD_HEADER
    + _PARENT2_BLOCK
    + """
# Task
I have two existing programs shown above (Current Program and Second Parent Program).
Please help me create a new program that has a totally different form from the given ones.
First, describe your new algorithm and main steps in one sentence. The description must be inside a brace. Next, """
    + _REWRITE_FORMAT_INSTRUCTIONS
)

EOH_E2_USER_TEMPLATE = (
    _SCAFFOLD_HEADER
    + _PARENT2_BLOCK
    + """
# Task
I have two existing programs shown above (Current Program and Second Parent Program).
Please help me create a new program that has a totally different form from the given ones but can be motivated from them.
Firstly, identify the common backbone idea in the provided programs. Secondly, based on the backbone idea describe your new algorithm in one sentence. The description must be inside a brace. Thirdly, """
    + _REWRITE_FORMAT_INSTRUCTIONS
)

EOH_M1_USER_TEMPLATE = (
    _SCAFFOLD_HEADER
    + """
# Task
Please assist me in creating a new program that has a different form but can be a modified version of the program provided.
First, describe your new algorithm and main steps in one sentence. The description must be inside a brace. Next, """
    + _DIFF_FORMAT_INSTRUCTIONS
)

EOH_M2_USER_TEMPLATE = (
    _SCAFFOLD_HEADER
    + """
# Task
Please identify the main algorithm parameters and assist me in creating a new program that has different parameter settings.
First, describe your new algorithm and main steps in one sentence. The description must be inside a brace. Next, """
    + _DIFF_FORMAT_INSTRUCTIONS
)

# NOEMA: m3 is EoH's one code-only operator (no brace-delimited thought) —
# has_thought=False below reflects this exactly.
EOH_M3_USER_TEMPLATE = (
    _SCAFFOLD_HEADER
    + """
# Task
First, identify the main components in the program above. Next, analyze whether any can be overfit to in-distribution instances. Then, simplify the components to enhance generalization to out-of-distribution instances. Finally, provide the revised code, keeping the function name, inputs, and outputs unchanged.
"""
    + _DIFF_FORMAT_INSTRUCTIONS
)

OPERATOR_MENU: Dict[str, OperatorSpec] = {
    "e1": OperatorSpec("e1", "eoh_e1_user", "full_rewrite", 2, True),
    "e2": OperatorSpec("e2", "eoh_e2_user", "full_rewrite", 2, True),
    "m1": OperatorSpec("m1", "eoh_m1_user", "diff", 1, True),
    "m2": OperatorSpec("m2", "eoh_m2_user", "diff", 1, True),
    "m3": OperatorSpec("m3", "eoh_m3_user", "diff", 1, False),
}

# Registered into TemplateManager by make_prompt_sampler() (noema/prompts.py).
OPERATOR_TEMPLATES: Dict[str, str] = {
    "eoh_e1_user": EOH_E1_USER_TEMPLATE,
    "eoh_e2_user": EOH_E2_USER_TEMPLATE,
    "eoh_m1_user": EOH_M1_USER_TEMPLATE,
    "eoh_m2_user": EOH_M2_USER_TEMPLATE,
    "eoh_m3_user": EOH_M3_USER_TEMPLATE,
}
