# 0041 — Persist a frozen, hashable run config

Added `NoemaConfig.to_dict`/`to_yaml` (dataclasses.asdict + yaml.safe_dump,
sort_keys=True) and wired `NoemaController.__init__` to write
`<output_dir>/config.yaml` once (guarded by `os.path.exists`, so checkpoint
resume never clobbers it), logging its sha256. Added the three required
tests to `tests/test_noema_controller.py`; `python3 -m unittest discover
tests` is green (128 tests).
