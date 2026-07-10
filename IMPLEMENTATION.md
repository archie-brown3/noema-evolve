# 0041 — Persist a frozen, hashable run config

Added `NoemaConfig.to_dict`/`to_yaml` (dataclasses.asdict + yaml.safe_dump,
sort_keys=True) and wired `NoemaController.__init__` to write
`<output_dir>/config.yaml` once (guarded by `os.path.exists`, so checkpoint
resume never clobbers it), logging its sha256. Added the three required
tests to `tests/test_noema_controller.py`; `python3 -m unittest discover
tests` is green (128 tests).

Blocked on: `git add` (any form — `-A`, or specific paths) was refused by
this session's approval gate every time it was attempted. No new files were
created, so all changes are visible via plain `git diff`/`git status`
without staging; flagging in case the verifier expects a staged index.
