"""Suite-wide pytest hooks for noema.

``tests/upstream/`` holds raw donor-repo dumps for fidelity review (task 0188).
They must not be collected or executed as part of the noema suite.
"""

from pathlib import Path

collect_ignore = ["upstream"]


def pytest_ignore_collect(collection_path, config):
    path = Path(collection_path)
    return "upstream" in path.parts
