#!/usr/bin/env bash
# The deterministic gate. Final vote on every piece of work — no model overrides it.
# The noema test suite already enforces the project's three guarantees:
# prompt identity, metering integrity, determinism.
set -e
cd "$(git rev-parse --show-toplevel)"
python3 -m unittest discover tests
