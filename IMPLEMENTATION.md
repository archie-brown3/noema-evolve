# 0038 · implement-verify-run-script

Added `loop/scripts/verify-run.sh` (7 checks, one per LIVE-RUNS §4 bullet),
a `verify-run` target on the repo-root `Makefile` (task named `loop/Makefile`,
which doesn't exist — added to the existing root Makefile instead, logged as
a deviation), `tests/fixtures/verify_run/` (pass + one corrupted fixture per
check), and `tests/test_verify_run.sh`.

**Blocked**: this session's Bash tool requires approval (unavailable,
autonomous run) for every `python3`, `bash <script>`, `make`, and `chmod`
invocation, so `tests/test_verify_run.sh`, `loop/guardrails/verify.sh`, and
the read-only run against `examples/circle_packing/noema_null_output` could
not be executed or independently verified here — traced by hand instead (one
real `set -e` bug caught and fixed this way). Full detail and exact commands
attempted are in the vault task's Output/notes
(`/root/claude-brain/tasks/0038-implement-verify-run-script.md`).
