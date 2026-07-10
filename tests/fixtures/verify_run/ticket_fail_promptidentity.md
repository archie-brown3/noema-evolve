---
ticket: RT-TEST-FAIL-PROMPT
status: verifying
config_sha256: 582f42e15c88f2e9962b247c98a53f1cedf89d7f9cfdb31844f735d9db3d1c4b
runs:
  - {arm: null, dir: tests/fixtures/verify_run/fail_promptidentity_a, seed: 42}
  - {arm: pes, dir: tests/fixtures/verify_run/fail_promptidentity_b, seed: 42}
---
## Purpose
Fixture ticket exercising the prompt-identity failure path: the two run dirs'
logged system prompts have different shared prefixes (before the `<<<COORD>>>`
coordination-suffix delimiter).
