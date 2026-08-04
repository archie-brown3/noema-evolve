"""CLI mutation prompt adaptation (agent host)."""

import unittest
from types import SimpleNamespace

from noema.agenthost.cli_prompt import (
    CLI_MUTATION_TASK,
    adapt_prompt_for_cli_mutation,
    adapt_prompt_user_for_cli_mutation,
    format_cli_population_catalog,
    prompt_uses_search_replace,
)
from noema.evolution.prompts import COORDINATION_HEADER


CONTROLLER_USER_TAIL = """# Task
Suggest improvements to the program that will improve its FITNESS SCORE.

You MUST use the exact SEARCH/REPLACE diff format shown below to indicate changes:

<<<<<<< SEARCH
# Original code to find and replace (must match exactly)
=======
# New replacement code
>>>>>>> REPLACE
"""

SAMPLE_USER = """# Current Program Information
- Fitness: 0.0000

# Program Evolution History
## Previous Attempts

## Top Performing Programs

### Program 1 (Score: 0.0000)
```python
def heilbronn_triangle11():
    return None
```
Key features: Performs well on combined_score (0.0000)

## Inspiration Programs

These programs represent diverse approaches and creative solutions that may inspire new ideas:

### Inspiration 1 (Score: 0.1000, Type: inspiration)
```python
def other():
    pass
```
Unique approach: different

# Current Program
```python
def heilbronn_triangle11():
    n = 11
    return n
```

# Second Parent Program
```python
def heilbronn_triangle11():
    return 1
```

""" + CONTROLLER_USER_TAIL


class TestCliMutationPrompt(unittest.TestCase):
    def test_adapt_replaces_search_replace_task(self):
        user = "# Current Program\n```python\npass\n```\n\n" + CONTROLLER_USER_TAIL
        adapted = adapt_prompt_user_for_cli_mutation(user)
        self.assertFalse(prompt_uses_search_replace(adapted))
        self.assertIn("Direct construction only", adapted)
        self.assertIn("entry-point signature exactly", adapted)
        self.assertIn("one iteration of the outer search loop", adapted)
        self.assertIn("submit_mutation", adapted)
        self.assertIn("MCP tools", adapted)
        self.assertIn("hill climbing", adapted)

    def test_adapt_preserves_coordination_suffix(self):
        user = (
            "# Current Program\n```python\npass\n```\n\n"
            + CONTROLLER_USER_TAIL
            + COORDINATION_HEADER
            + "Try a grid-based placement."
        )
        adapted = adapt_prompt_user_for_cli_mutation(user)
        self.assertIn(COORDINATION_HEADER + "Try a grid-based placement.", adapted)
        self.assertFalse(prompt_uses_search_replace(adapted))

    def test_adapt_prompt_dict_keeps_system_message(self):
        prompt = {
            "system": (
                "SETTING:\nExpert on the problem.\n\n"
                "PERFORMANCE METRICS:\n1. score\n\n"
                "TECHNICAL REQUIREMENTS:\n"
                "- **Determinism**: Use fixed random seeds if employing stochastic methods."
            ),
            "user": CONTROLLER_USER_TAIL,
        }
        adapted = adapt_prompt_for_cli_mutation(prompt)
        self.assertIn("HOST SESSION ROLE", adapted["system"])
        self.assertIn("you are not the optimizer", adapted["system"])
        self.assertIn("same function name", adapted["system"])
        self.assertIn("No hill climbing", adapted["system"])
        self.assertNotIn("TECHNICAL REQUIREMENTS", adapted["system"])
        self.assertNotIn("stochastic methods", adapted["system"])
        self.assertIn(CLI_MUTATION_TASK.splitlines()[0], adapted["user"])

    def test_cli_prompt_replaces_top_code_with_catalog_keeps_parent_and_parent2(self):
        catalog = [
            SimpleNamespace(id="initial", fitness=0.0),
            SimpleNamespace(id="it000000", fitness=0.0253),
        ]
        adapted = adapt_prompt_user_for_cli_mutation(
            SAMPLE_USER, catalog_programs=catalog
        )
        self.assertIn("## Population catalog", adapted)
        self.assertIn("`it000000` — fitness 0.0253", adapted)
        self.assertIn("get_program", adapted)
        self.assertNotIn("### Program 1 (Score:", adapted)
        self.assertNotIn("## Inspiration Programs", adapted)
        self.assertNotIn("def other():", adapted)
        self.assertIn("# Current Program", adapted)
        self.assertIn("n = 11", adapted)
        self.assertIn("# Second Parent Program", adapted)
        self.assertIn("return 1", adapted)
        self.assertFalse(prompt_uses_search_replace(adapted))

    def test_format_catalog_dedupes_and_handles_empty(self):
        text = format_cli_population_catalog([])
        self.assertIn("(empty", text)
        text = format_cli_population_catalog(
            [
                {"id": "a", "fitness": 1.0},
                SimpleNamespace(id="a", fitness=0.5),
                {"id": "b", "metrics": {"combined_score": 0.25}},
            ]
        )
        self.assertEqual(text.count("`a`"), 1)
        self.assertIn("`b`", text)


if __name__ == "__main__":
    unittest.main()
