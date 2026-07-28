"""Export a Noema run into EvoReplay's refined checkpoint layout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

import yaml

CHECKPOINT_RE = re.compile(r"checkpoint_(\d+)$")
PROGRAM_FIELDS = (
    "id",
    "language",
    "metrics",
    "iteration_found",
    "parent_id",
    "other_context_ids",
    "parent_info",
    "context_info",
    "timestamp",
    "metadata",
    "generation",
)


def _jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")


def _checkpoints(run: Path) -> list[tuple[int, Path]]:
    result = []
    root = run / "checkpoints"
    if root.is_dir():
        for child in root.iterdir():
            match = CHECKPOINT_RE.fullmatch(child.name)
            if match and child.is_dir():
                result.append((int(match.group(1)), child))
    return sorted(result)


def _blob(root: Path, content: bytes, extension: str) -> str:
    sha = hashlib.sha256(content).hexdigest()
    path = root / sha[:2] / f"{sha}.{extension}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return sha


def _program_row(
    program: dict[str, Any], blobs: Path, source: str = "checkpoint.programs"
) -> dict[str, Any]:
    row = {field: program.get(field) for field in PROGRAM_FIELDS}
    row["status"] = "accepted"
    row["source"] = source
    code = program.get("code")
    row["solution_sha256"] = (
        _blob(blobs, code.encode("utf-8"), "txt") if isinstance(code, str) else None
    )
    prompts = program.get("prompts")
    row["prompts_sha256"] = (
        _blob(
            blobs,
            json.dumps(prompts, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            "json",
        )
        if prompts
        else None
    )
    return row


def _redact_secrets(value: Any) -> Any:
    exact_secret_keys = {
        "api_key",
        "authorization",
        "cookie",
        "set_cookie",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "client_secret",
        "credentials",
        "private_key",
    }

    def is_secret_key(key: Any) -> bool:
        normalized = str(key).lower().replace("-", "_")
        return normalized in exact_secret_keys or normalized.endswith(
            (
                "_api_key",
                "_password",
                "_secret",
                "_credential",
                "_credentials",
                "_token",
                "_access_key",
                "_private_key",
            )
        )

    if isinstance(value, dict):
        return {
            key: "<redacted>" if is_secret_key(key) else _redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _write_redacted_config(source: Path, destination: Path) -> None:
    config = yaml.safe_load(source.read_text()) or {}
    destination.write_text(yaml.safe_dump(_redact_secrets(config), sort_keys=True))


def _membership_rows(iteration: int, metadata: dict[str, Any]):
    for island, members in enumerate(metadata.get("islands", [])):
        for slot, program_id in enumerate(members):
            yield {
                "iteration": iteration,
                "role": "island_member",
                "island_idx": island,
                "slot_key": slot,
                "program_id": program_id,
                "value": None,
            }
    for island, program_id in enumerate(metadata.get("island_best_programs", [])):
        yield {
            "iteration": iteration,
            "role": "island_best",
            "island_idx": island,
            "slot_key": None,
            "program_id": program_id,
            "value": None,
        }
    for slot, program_id in enumerate(metadata.get("archive", [])):
        yield {
            "iteration": iteration,
            "role": "archive",
            "island_idx": None,
            "slot_key": slot,
            "program_id": program_id,
            "value": None,
        }


def _store_state(checkpoint: Path) -> tuple[str, dict[str, Any]] | None:
    for kind, filename in (("cvt", "cvt_store.json"), ("tree", "tree_store.json")):
        path = checkpoint / filename
        if path.exists():
            return kind, json.loads(path.read_text())
    return None


def _store_membership_rows(iteration: int, kind: str, state: dict[str, Any]):
    if kind == "cvt":
        for program_id, cell in sorted(state.get("cell_of", {}).items()):
            yield {
                "iteration": iteration,
                "role": "cvt_cell",
                "slot_key": cell,
                "program_id": program_id,
                "value": None,
            }
        for cell, program_id in sorted(
            state.get("elite_of", {}).items(), key=lambda item: int(item[0])
        ):
            yield {
                "iteration": iteration,
                "role": "cvt_elite",
                "slot_key": int(cell),
                "program_id": program_id,
                "value": None,
            }
    else:
        for slot, program_id in enumerate(state.get("working_set_ids", [])):
            yield {
                "iteration": iteration,
                "role": "tree_working_set",
                "slot_key": slot,
                "program_id": program_id,
                "value": None,
            }
        for program_id, branch in sorted(state.get("branches", {}).items()):
            yield {
                "iteration": iteration,
                "role": "tree_branch",
                "slot_key": branch,
                "program_id": program_id,
                "value": None,
            }


def _ledger_scalars(checkpoint: Path):
    state_path = checkpoint / "noema_state.json"
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text())
    totals: dict[tuple[int, str], dict[str, float]] = {}
    for record in state.get("ledger", {}).get("records", []):
        iteration = int(record.get("iteration", -1))
        account = str(record.get("account", "unknown"))
        total = totals.setdefault((iteration, account), {"tokens": 0, "cost": 0.0})
        total["tokens"] += int(record.get("prompt_tokens", 0)) + int(
            record.get("completion_tokens", 0)
        )
        total["cost"] += float(record.get("cost", 0.0))
    for (iteration, account), values in sorted(totals.items()):
        yield {
            "iteration": iteration,
            "key": f"{account}_tokens",
            "value": values["tokens"],
        }
        yield {
            "iteration": iteration,
            "key": f"{account}_cost",
            "value": values["cost"],
        }


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _trace_programs(
    attempts: list[dict[str, Any]], evolution: list[dict[str, Any]], blobs: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evolution_by_child = {
        row.get("child_id"): row for row in evolution if row.get("child_id")
    }
    programs = []
    iterations = []
    seen = set()

    def add(program: dict[str, Any], source: str) -> None:
        program_id = program.get("id")
        if not program_id or program_id in seen:
            return
        seen.add(program_id)
        program.setdefault("language", "python")
        programs.append(_program_row(program, blobs, source))

    for attempt in attempts:
        if attempt.get("outcome") != "accepted":
            continue
        parent = attempt.get("parent")
        if isinstance(parent, dict):
            add(dict(parent), "attempt_trace.parent")

        candidate = attempt.get("candidate")
        if not isinstance(candidate, dict) or not candidate.get("id"):
            continue
        trace = evolution_by_child.get(candidate["id"], {})
        program = {
            "id": candidate["id"],
            "code": candidate.get("code"),
            "language": parent.get("language", "python") if isinstance(parent, dict) else "python",
            "metrics": trace.get("child_metrics"),
            "iteration_found": attempt.get("iteration"),
            "parent_id": trace.get("parent_id")
            or (parent.get("id") if isinstance(parent, dict) else None),
            "metadata": attempt.get("metadata", {}),
            "generation": trace.get("generation", attempt.get("generation")),
            "timestamp": trace.get("timestamp", attempt.get("timestamp")),
            "prompts": attempt.get("prompt"),
        }
        add(program, "attempt_trace.candidate")
        iterations.append(
            {
                "iteration": attempt.get("iteration"),
                "role": "accepted_attempt",
                "slot_key": attempt.get("attempt_id"),
                "program_id": candidate["id"],
                "value": None,
            }
        )
    return programs, iterations


def export_run(run_dir: os.PathLike[str] | str, output_dir: os.PathLike[str] | str) -> Path:
    run = Path(run_dir)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"destination already exists: {output}")
    checkpoints = _checkpoints(run)
    attempt_path = run / "attempt_trace.jsonl"
    attempt_rows = _jsonl_rows(attempt_path)
    if not checkpoints and not attempt_rows:
        raise FileNotFoundError(f"no checkpoints or attempt trace found in {run}")

    output.mkdir(parents=True)
    blobs = output / "blobs"
    blobs.mkdir()

    config = run / "config.yaml"
    if config.exists():
        _write_redacted_config(config, output / "run_config.yaml")

    programs = []
    seen = set()
    iterations = []
    scalars = []
    if checkpoints:
        for iteration, checkpoint in checkpoints:
            program_dir = checkpoint / "programs"
            if program_dir.is_dir():
                for path in sorted(program_dir.glob("*.json")):
                    program = json.loads(path.read_text())
                    if program.get("id") not in seen:
                        seen.add(program.get("id"))
                        programs.append(_program_row(program, blobs))
            store = _store_state(checkpoint)
            if store is not None:
                kind, state = store
                for program_id in sorted(state.get("programs", {})):
                    program = state["programs"][program_id]
                    if program_id not in seen:
                        seen.add(program_id)
                        programs.append(_program_row(program, blobs, f"{kind}_store.programs"))
                iterations.extend(_store_membership_rows(iteration, kind, state))
                if "last_iteration" in state:
                    scalars.append(
                        {
                            "iteration": iteration,
                            "key": "last_iteration",
                            "value": state["last_iteration"],
                        }
                    )
            metadata_path = checkpoint / "metadata.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text())
                iterations.extend(_membership_rows(iteration, metadata))
                for key in ("best_program_id", "last_iteration", "current_island"):
                    if key in metadata:
                        scalars.append({"iteration": iteration, "key": key, "value": metadata[key]})
        scalars.extend(_ledger_scalars(checkpoints[-1][1]))
    else:
        programs, iterations = _trace_programs(
            attempt_rows, _jsonl_rows(run / "evolution_trace.jsonl"), blobs
        )
    _jsonl(output / "programs.jsonl", programs)
    _jsonl(output / "iterations.jsonl", iterations)
    _jsonl(output / "iter_scalars.jsonl", scalars)

    if attempt_path.exists():
        shutil.copyfile(attempt_path, output / "noema_attempts.jsonl")
    else:
        (output / "noema_attempts.jsonl").touch()

    selections = run / "selection_trace.jsonl"
    if selections.exists():
        shutil.copyfile(selections, output / "noema_selections.jsonl")
    else:
        (output / "noema_selections.jsonl").touch()

    meta = {
        "backend": "noema",
        "source": run.name,
        "counts": {
            "checkpoints": len(checkpoints),
            "accepted_unique": len(programs),
            "attempts": len(attempt_rows),
        },
    }
    (output / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    export_run(args.run_dir, args.output)


if __name__ == "__main__":
    main()
