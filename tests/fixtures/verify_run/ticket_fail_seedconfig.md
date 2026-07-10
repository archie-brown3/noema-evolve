---
ticket: RT-TEST-FAIL-SEEDCONFIG
status: verifying
config_sha256: faabe54217ec8d40d5c7a36017c08be22a80e533bb066cd3f80447ec8e281329
runs:
  - {arm: null, dir: tests/fixtures/verify_run/fail_seedconfig, seed: 42}
---
## Purpose
Fixture ticket exercising the seed/config-sha-match failure path: the ticket's
recorded `config_sha256` does not match the run dir's `run_manifest.json`.
Single-run ticket, so the paired-arm checks (5, 6) do not activate.
