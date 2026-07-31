#!/usr/bin/env python3
"""Snapshot noema's installed OpenEvolve prompt bundle for the stock control."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import shutil
from pathlib import Path

import openevolve.prompt.templates as templates


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("prompt-normalized-templates"),
    )
    args = parser.parse_args()
    module_path = Path(inspect.getfile(templates)).resolve()
    source = module_path.parent.parent / "prompts" / "defaults"
    if not source.is_dir():
        raise SystemExit(f"installed prompt bundle not found: {source}")
    args.output.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and (path.suffix in {".txt", ".json"}):
            shutil.copy2(path, args.output / path.name)
    manifest = {
        "source_module": str(module_path),
        "source_bundle": str(source),
        "files": {
            path.name: sha256(path)
            for path in sorted(args.output.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Snapshotted {len(manifest['files'])} prompt files to {args.output}")


if __name__ == "__main__":
    main()
