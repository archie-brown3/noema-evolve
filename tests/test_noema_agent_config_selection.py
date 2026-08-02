"""Config discovery/loading tests for the agency CLI."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import yaml

from noema.agenthost.cli_bootstrap import build_agent_config, parse_entry_args
from noema.agenthost.configure_files import discover_example, load_noema_and_agent
from noema.agenthost.configure_tui import _agent_sections
from noema.agenthost.factory import create_agent_session
from noema.agenthost.mutation import FakeMutationBackend
from noema.coordination import NullCoordination

ROOT = Path(__file__).parents[1]
CIRCLE_PACKING = ROOT / "examples" / "circle_packing"
EVALUATOR = CIRCLE_PACKING / "evaluator.py"
INITIAL_PROGRAM = CIRCLE_PACKING / "initial_program.py"


class TestCirclePackingConfigSelection(unittest.TestCase):
    def test_cli_discovers_every_existing_yaml_config(self):
        found = discover_example(CIRCLE_PACKING)
        expected = {path.resolve() for path in CIRCLE_PACKING.glob("*.yaml")}

        self.assertEqual(set(found.config_candidates), expected)

    def test_cli_config_field_exposes_every_discovered_path(self):
        found = discover_example(CIRCLE_PACKING)
        fields = _agent_sections(
            load_noema_and_agent(found.preferred_config),
            found,
            CIRCLE_PACKING / "noema_agent_output",
        )
        config_field = fields["paths"][0]

        self.assertEqual(
            set(config_field["choices"]),
            {path.name for path in CIRCLE_PACKING.glob("*.yaml")} | {"(new) config.yaml"},
        )

    def test_cli_loads_every_config_and_preserves_selected_values(self):
        for path in sorted(CIRCLE_PACKING.glob("*.yaml")):
            with self.subTest(config=path.name):
                raw = yaml.safe_load(path.read_text()) or {}
                loaded = load_noema_and_agent(path)

                self.assertEqual(loaded.noema.max_iterations, raw["max_iterations"])
                self.assertEqual(loaded.noema.checkpoint_interval, raw["checkpoint_interval"])
                self.assertEqual(
                    loaded.noema.diff_based_evolution,
                    raw["diff_based_evolution"],
                )
                self.assertEqual(
                    loaded.noema.prompt.system_message,
                    raw["prompt"]["system_message"],
                )
                self.assertEqual(
                    loaded.noema.prompt.use_template_stochasticity,
                    False,
                )
                self.assertEqual(
                    loaded.noema.database.population_size,
                    raw["database"]["population_size"],
                )
                self.assertEqual(
                    loaded.noema.evaluator.timeout,
                    raw["evaluator"]["timeout"],
                )

    def test_cli_bootstrap_and_session_reflect_the_selected_config_in_full(self):
        for path in sorted(CIRCLE_PACKING.glob("*.yaml")):
            with self.subTest(config=path.name), tempfile.TemporaryDirectory() as tmp:
                args = parse_entry_args(
                    [
                        "--config",
                        str(path),
                        "--evaluation-file",
                        str(EVALUATOR),
                        "--initial-program",
                        str(INITIAL_PROGRAM),
                        "--output-dir",
                        str(Path(tmp) / "output"),
                    ]
                )
                selected = build_agent_config(args)
                session = create_agent_session(
                    selected,
                    evaluation_file=str(EVALUATOR),
                    initial_program_code=INITIAL_PROGRAM.read_text(),
                    output_dir=str(Path(tmp) / "output"),
                    mutation_backend=FakeMutationBackend(fail_error="not run"),
                    coordination=NullCoordination(),
                )
                try:
                    self.assertEqual(session.config.to_dict(), selected.noema.to_dict())
                    self.assertEqual(session.config.max_iterations, selected.noema.max_iterations)
                    self.assertEqual(
                        asdict(session.config.database), asdict(selected.noema.database)
                    )
                    self.assertEqual(
                        asdict(session.config.evaluator), asdict(selected.noema.evaluator)
                    )
                    self.assertEqual(asdict(session.config.prompt), asdict(selected.noema.prompt))
                    self.assertEqual(asdict(session.config.llm), asdict(selected.noema.llm))
                finally:
                    session.run_logging.detach()


if __name__ == "__main__":
    unittest.main()
