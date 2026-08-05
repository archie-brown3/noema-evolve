"""Suite-wide pytest hooks for noema.

``tests/upstream/`` holds raw donor-repo dumps for fidelity review (task 0188).
They must not be collected or executed as part of the noema suite.
"""

from pathlib import Path

collect_ignore = ["upstream"]


def pytest_ignore_collect(collection_path, config):
    # Return None, not False, when we have no opinion: this hook is firstresult,
    # so a literal False here WINS over every other opinion — including pytest's
    # own handling of --ignore/--ignore-glob, which was silently doing nothing
    # for the whole suite.
    if "upstream" in Path(collection_path).parts:
        return True
    return None
