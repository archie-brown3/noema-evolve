"""
HiFo-Prompt coordination mechanism, transplanted for noema.

The mechanism (insight pool + evolutionary navigator + credit assignment +
insight extraction) is copied from the released HiFo-Prompt code and isolated
in this package; HiFo's substrate (its EoH evolution loop, operator prompts,
response parsing, LLM interface) is NOT ported — noema's controller and
OpenEvolve components replace it. See PLAN.md section 2.2 for the
mechanism/substrate boundary and the documented deviations from the released
code.

Borrowed source: https://github.com/Challenger-XJTU/HiFo-Prompt
(files under hifo/src/hifo/methods/hifo/). Files in this package carry
per-file provenance headers; every local modification is marked with a
"NOEMA:" comment.
"""

from noema.coordination.hifo.insight_pool import InsightPool
from noema.coordination.hifo.evolutionary_navigator import EvolutionaryNavigator
from noema.coordination.hifo.module import HiFoPromptModule

__all__ = ["InsightPool", "EvolutionaryNavigator", "HiFoPromptModule"]
