"""Tests for Noema's shared OpenEvolve-style run logging."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from noema.config import NoemaConfig
from noema.logging import (
    LoggingConfig,
    format_accepted_child_line,
    setup_run_logging,
)


class TestLoggingConfig(unittest.TestCase):
    def test_defaults_and_nested_yaml_round_trip(self):
        config = NoemaConfig.from_dict({"logging": {"level": "info"}})
        self.assertEqual(config.logging.level, "INFO")
        self.assertIsNone(config.logging.log_dir)
        self.assertTrue(config.logging.console)
        self.assertTrue(config.logging.file)

    def test_bad_level_and_nested_key_rejected(self):
        with self.assertRaises(ValueError):
            LoggingConfig(level="verbose")
        with self.assertRaises(ValueError):
            NoemaConfig.from_dict({"logging": {"levle": "INFO"}})


class TestRunLogging(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_noema_run_logging", False):
                root.removeHandler(handler)
                handler.close()

    def test_setup_creates_file_and_replaces_only_noema_handlers(self):
        foreign = logging.NullHandler()
        root = logging.getLogger()
        root.addHandler(foreign)
        with tempfile.TemporaryDirectory() as tmp:
            first = setup_run_logging(LoggingConfig(), tmp)
            second = setup_run_logging(LoggingConfig(), tmp)
            tagged = [
                handler
                for handler in root.handlers
                if getattr(handler, "_noema_run_logging", False)
            ]
            self.assertLessEqual(len(tagged), 2)
            self.assertEqual(second.log_path.parent, Path(tmp) / "logs")
            self.assertTrue(second.log_path.exists())
            self.assertIn(foreign, root.handlers)
            first.detach()
            second.detach()
        root.removeHandler(foreign)

    def test_console_suspension_and_restoration_preserve_handler_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            handle = setup_run_logging(
                LoggingConfig(level="INFO", file=False),
                tmp,
            )
            self.assertIsNotNone(handle.console_handler)
            assert handle.console_handler is not None
            handle.console_handler.setLevel(logging.WARNING)

            handle.suspend_console()
            self.assertGreater(handle.console_handler.level, logging.CRITICAL)

            handle.restore_console()
            self.assertEqual(handle.console_handler.level, logging.WARNING)

            handle.suspend_console()
            handle.suspend_console()
            handle.restore_console()
            self.assertEqual(handle.console_handler.level, logging.WARNING)
            handle.detach()

    def test_format_matches_openevolve_shape_and_delta(self):
        line = format_accepted_child_line(
            iteration=12,
            child_id="abc123",
            parent_id="def456",
            elapsed_s=8.42,
            metrics={"combined_score": 0.8123},
            parent_metrics={"combined_score": 0.7813},
            via="cli/shallow",
        )
        self.assertEqual(
            line,
            "Iteration 12: Child abc123 from parent def456 via cli/shallow "
            "in 8.42s. Metrics: combined_score=0.8123 (Δ: +0.0310)",
        )


if __name__ == "__main__":
    unittest.main()
