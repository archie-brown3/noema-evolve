---
ticket: RT-TEST-PASS
status: verifying
config_sha256: 582f42e15c88f2e9962b247c98a53f1cedf89d7f9cfdb31844f735d9db3d1c4b
runs:
  - {arm: null, dir: tests/fixtures/verify_run/pass, seed: 42}
  - {arm: pes, dir: tests/fixtures/verify_run/pass_partner, seed: 42}
---
## Purpose
Fixture ticket for verify-run.sh's own test suite (tests/test_verify_run.sh):
exercises the paired-arm checks (prompt-identity, config-delta) and
seed/config-sha-match against a matching pair of synthetic run dirs.
