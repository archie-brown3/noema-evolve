"""Per-role model selection: cheap mutation seat, expensive coordination seat.

The two seats were already budgeted separately (MUTATION_ACCOUNT /
COORDINATION_ACCOUNT); this covers them differing in *model* too, plus the
backward compatibility that keeps every pre-split config loadable.
"""

import os
import tempfile
import textwrap
import unittest

import yaml

from noema.config import LLMClientConfig, LLMRolesConfig, NoemaConfig
from noema.controller import NoemaController

FLAT_CONFIG = textwrap.dedent(
    """
    max_iterations: 5
    random_seed: 42
    llm:
      model: qwen-14b
      api_base: http://cluster:8000/v1
      api_key: none
      temperature: 0.7
      max_tokens: 4096
      timeout: 300
    coordination:
      module: hifo
    """
)

PER_ROLE_CONFIG = textwrap.dedent(
    """
    max_iterations: 5
    random_seed: 42
    llm:
      mutation:
        model: qwen-14b
        api_base: http://cluster:8000/v1
        api_key: none
        temperature: 0.9
      coordination:
        model: claude-opus-4-8
        api_base: https://openrouter.ai/api/v1
        api_key: none
        temperature: 0.3
    coordination:
      module: hifo
    """
)


class TestFlatConfigStillLoads(unittest.TestCase):
    """The pre-split shape must keep working: existing configs and the frozen
    run configs under runs/ have to stay loadable for checkpoint resume."""

    def test_flat_llm_block_feeds_both_seats(self):
        config = NoemaConfig.from_dict(yaml.safe_load(FLAT_CONFIG))
        self.assertEqual(config.llm.mutation.model, "qwen-14b")
        self.assertEqual(config.llm.coordination.model, "qwen-14b")
        # non-model fields propagate to both seats too
        self.assertEqual(config.llm.mutation.api_base, "http://cluster:8000/v1")
        self.assertEqual(config.llm.coordination.timeout, 300)

    def test_seats_are_independent_objects(self):
        """A single shared instance would make one seat's edit silently move
        the other — and both seats land in the frozen config."""
        config = NoemaConfig.from_dict(yaml.safe_load(FLAT_CONFIG))
        self.assertIsNot(config.llm.mutation, config.llm.coordination)
        config.llm.coordination.model = "claude-opus-4-8"
        self.assertEqual(config.llm.mutation.model, "qwen-14b")


class TestPerRoleConfig(unittest.TestCase):
    def test_each_seat_keeps_its_own_settings(self):
        config = NoemaConfig.from_dict(yaml.safe_load(PER_ROLE_CONFIG))
        self.assertEqual(config.llm.mutation.model, "qwen-14b")
        self.assertEqual(config.llm.coordination.model, "claude-opus-4-8")
        self.assertEqual(config.llm.mutation.temperature, 0.9)
        self.assertEqual(config.llm.coordination.temperature, 0.3)

    def test_half_specified_block_fails_loud(self):
        """Naming one seat and not the other would silently run the unnamed one
        on the gpt-4o-mini default — an arm-defining setting, wrong, no error.
        Same failure class as the task 0056 typo guard."""
        data = yaml.safe_load(PER_ROLE_CONFIG)
        del data["llm"]["mutation"]
        with self.assertRaises(ValueError) as cm:
            NoemaConfig.from_dict(data)
        self.assertIn("mutation", str(cm.exception))

    def test_typo_inside_a_seat_is_rejected(self):
        """The task 0056 guard must keep covering the llm section now that it
        nests: `temperatur:` inside a seat is otherwise silently dropped."""
        data = yaml.safe_load(PER_ROLE_CONFIG)
        data["llm"]["coordination"]["temperatur"] = 0.3
        with self.assertRaises(ValueError) as cm:
            NoemaConfig.from_dict(data)
        self.assertIn("temperatur", str(cm.exception))

    def test_typo_in_a_flat_block_is_still_rejected(self):
        data = yaml.safe_load(FLAT_CONFIG)
        data["llm"]["modell"] = "qwen-14b"
        with self.assertRaises(ValueError) as cm:
            NoemaConfig.from_dict(data)
        self.assertIn("modell", str(cm.exception))


class TestControllerWiresSeatsToTheirModels(unittest.TestCase):
    """The wiring check. Existing end-to-end tests inject `mutation_llm`, which
    bypasses config->model resolution entirely, so nothing else covers this."""

    def _controller(self, tmp, config):
        eval_path = os.path.join(tmp, "evaluator.py")
        with open(eval_path, "w") as f:
            f.write("def evaluate(path):\n    return {'combined_score': 0.0}\n")
        return NoemaController(
            config=config,
            evaluation_file=eval_path,
            initial_program_code="x = 1\n",
            output_dir=os.path.join(tmp, "output"),
        )

    def test_seats_get_their_configured_models(self):
        config = NoemaConfig.from_dict(yaml.safe_load(PER_ROLE_CONFIG))
        with tempfile.TemporaryDirectory() as tmp:
            controller = self._controller(tmp, config)
            self.assertEqual(controller.mutation_llm.model, "qwen-14b")
            self.assertEqual(controller.coordination.llm.model, "claude-opus-4-8")
            # the seats stay on their own ledger accounts
            self.assertEqual(controller.mutation_llm.account, "mutation")
            self.assertEqual(controller.coordination.llm.account, "coordination")

    def test_single_model_config_puts_one_model_on_both_seats(self):
        config = NoemaConfig(
            llm=LLMRolesConfig(
                mutation=LLMClientConfig(model="qwen-14b", api_key="none"),
                coordination=LLMClientConfig(model="qwen-14b", api_key="none"),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            controller = self._controller(tmp, config)
            self.assertEqual(controller.mutation_llm.model, "qwen-14b")
            self.assertEqual(controller.coordination.llm.model, "qwen-14b")


if __name__ == "__main__":
    unittest.main()
