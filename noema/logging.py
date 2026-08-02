"""Central OpenEvolve-style logging for Noema run hosts."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_HANDLER_TAG = "_noema_run_logging"


@dataclass
class LoggingConfig:
    """Configuration for a Noema run's console and text-file logging."""

    level: str = "INFO"
    log_dir: Optional[str] = None
    console: bool = True
    file: bool = True

    def __post_init__(self) -> None:
        level = str(self.level).upper()
        if level not in _VALID_LEVELS:
            raise ValueError(
                f"logging.level must be one of {sorted(_VALID_LEVELS)}, got {self.level!r}"
            )
        self.level = level


@dataclass
class RunLoggingHandle:
    """References to handlers installed for one Noema run."""

    log_dir: Optional[Path]
    log_path: Optional[Path]
    level: int
    console_handler: Optional[logging.Handler] = None
    file_handler: Optional[logging.Handler] = None
    _console_previous_level: Optional[int] = None

    def suspend_console(self) -> None:
        """Temporarily disable this run's stdio handler for Textual rendering."""

        if self.console_handler is None:
            return
        if self._console_previous_level is None:
            self._console_previous_level = self.console_handler.level
        self.console_handler.setLevel(logging.CRITICAL + 1)

    def restore_console(self) -> None:
        """Restore the console handler state captured by ``suspend_console``."""

        if self.console_handler is None or self._console_previous_level is None:
            return
        self.console_handler.setLevel(self._console_previous_level)
        self._console_previous_level = None

    def detach(self) -> None:
        """Remove and close only the handlers owned by this run."""

        root = logging.getLogger()
        for handler in (self.console_handler, self.file_handler):
            if handler is None:
                continue
            root.removeHandler(handler)
            handler.close()


def host_logger() -> logging.Logger:
    """Return the shared compact host-progress logger."""

    return logging.getLogger("noema.host")


def setup_run_logging(config: LoggingConfig, output_dir: str | Path) -> RunLoggingHandle:
    """Install one idempotent Noema console/file logging configuration."""

    if not isinstance(config, LoggingConfig):
        raise TypeError(f"expected LoggingConfig, got {type(config)!r}")

    level = logging._nameToLevel[config.level]
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)

    output_path = Path(output_dir)
    log_dir = Path(config.log_dir) if config.log_dir else output_path / "logs"
    if config.file:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path: Optional[Path] = log_dir / (
            f"noema_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
    else:
        log_path = None

    console_handler: Optional[logging.Handler] = None
    if config.console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        setattr(console_handler, _HANDLER_TAG, True)
        root.addHandler(console_handler)

    file_handler: Optional[logging.Handler] = None
    if log_path is not None:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        setattr(file_handler, _HANDLER_TAG, True)
        root.addHandler(file_handler)

    return RunLoggingHandle(
        log_dir=log_dir if config.file else None,
        log_path=log_path,
        level=level,
        console_handler=console_handler,
        file_handler=file_handler,
    )


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return f"{value:.4f}"
    return str(value)


def _numeric_metrics(metrics: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not isinstance(metrics, dict):
        return {}
    return {
        key: value
        for key, value in metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def format_metrics_compact(metrics: Optional[Dict[str, Any]]) -> str:
    """Format scalar metrics with ``combined_score`` first when present."""

    numeric = _numeric_metrics(metrics)
    keys = sorted(numeric)
    if "combined_score" in numeric:
        keys.remove("combined_score")
        keys.insert(0, "combined_score")
    return ", ".join(f"{key}={_format_number(numeric[key])}" for key in keys) or "none"


def format_delta(
    metrics: Optional[Dict[str, Any]], parent_metrics: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Format the combined-score improvement when both scores are numeric."""

    current = _numeric_metrics(metrics)
    parent = _numeric_metrics(parent_metrics)
    if "combined_score" not in current or "combined_score" not in parent:
        return None
    return f"{current['combined_score'] - parent['combined_score']:+.4f}"


def format_accepted_child_line(
    *,
    iteration: int,
    child_id: str,
    parent_id: str,
    elapsed_s: float,
    metrics: Optional[Dict[str, Any]],
    parent_metrics: Optional[Dict[str, Any]],
    via: Optional[str] = None,
) -> str:
    """Format one OpenEvolve-style accepted-child heartbeat."""

    via_text = f" via {via}" if via else ""
    delta = format_delta(metrics, parent_metrics)
    metrics_text = format_metrics_compact(metrics)
    if delta is not None:
        metrics_text = f"{metrics_text} (Δ: {delta})"
    return (
        f"Iteration {iteration}: Child {child_id} from parent {parent_id}{via_text} "
        f"in {elapsed_s:.2f}s. Metrics: {metrics_text}"
    )
