"""Append-only provenance for every mutation attempt."""

import base64
import hashlib
import json
import os
import subprocess
import time
from enum import Enum
from typing import Any, Dict, Optional

ATTEMPT_OUTCOMES = frozenset(
    {
        "accepted",
        "acceptance_failure",
        "budget_exhausted",
        "evaluation_failure",
        "immutable_boundary_violation",
        "non_improvement",
        "over_length",
        "provider_failure",
        "unparseable_response",
    }
)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_provenance(path: str) -> Dict[str, Any]:
    """Best-effort revision and dirty-state snapshot for the running checkout."""

    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        patch = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        digest = hashlib.sha256()
        digest.update(patch)
        for encoded_path in sorted(item for item in untracked if item):
            digest.update(encoded_path)
            with open(os.path.join(root, os.fsdecode(encoded_path)), "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    digest.update(chunk)
        dirty = bool(patch or any(untracked))
        return {
            "revision": revision,
            "dirty": dirty,
            "dirty_sha256": digest.hexdigest() if dirty else None,
        }
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "dirty": None, "dirty_sha256": None}


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__noema_bytes_base64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value


class AttemptTraceWriter:
    """Write one self-contained JSONL record per attempted mutation."""

    def __init__(
        self,
        path: str,
        *,
        run_id: Optional[str] = None,
        config_sha256: Optional[str] = None,
        git_provenance: Optional[Dict[str, Any]] = None,
    ):
        self.path = path
        self.run_id = run_id or os.path.basename(os.path.abspath(os.path.dirname(path)))
        self.config_sha256 = config_sha256
        self.git_provenance = dict(git_provenance or {})

    def attempt_id(self, iteration: int, attempt: int) -> str:
        return f"{self.run_id}:{iteration:06d}:{attempt:02d}"

    def write(self, *, iteration: int, attempt: int, outcome: str, **fields: Any) -> None:
        if outcome not in ATTEMPT_OUTCOMES:
            raise ValueError(f"unknown attempt outcome: {outcome}")
        record = {
            "schema_version": 1,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id(iteration, attempt),
            "iteration": iteration,
            "attempt": attempt,
            "outcome": outcome,
            "timestamp": time.time(),
            "config_sha256": self.config_sha256,
            "git": self.git_provenance,
            **fields,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(_json_safe(record), sort_keys=True) + "\n")

    def write_selection(
        self,
        *,
        iteration: int,
        program_id: str,
        selected_attempt_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        if status not in {"accepted", "failed"}:
            raise ValueError(f"unknown selection status: {status}")
        record = {
            "schema_version": 1,
            "run_id": self.run_id,
            "iteration": iteration,
            "program_id": program_id,
            "selected_attempt_id": selected_attempt_id,
            "status": status,
            "error": error,
            "timestamp": time.time(),
        }
        path = os.path.join(os.path.dirname(self.path), "selection_trace.jsonl")
        with open(path, "a") as f:
            f.write(json.dumps(_json_safe(record), sort_keys=True) + "\n")
