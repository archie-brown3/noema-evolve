---
ticket: RT-TEST-FAIL-CONFIG
status: verifying
config_sha256: 582f42e15c88f2e9962b247c98a53f1cedf89d7f9cfdb31844f735d9db3d1c4b
runs:
  - {arm: null, dir: tests/fixtures/verify_run/fail_configdelta_a, seed: 42}
  - {arm: pes, dir: tests/fixtures/verify_run/fail_configdelta_b, seed: 42}
---
## Purpose
Fixture ticket exercising the config-delta failure path: the two run dirs'
effective configs differ in `num_top_programs` in addition to
`coordination_module`, violating the paired-arm contract.
