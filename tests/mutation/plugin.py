"""0188 Stage 5 — mutation-run pytest plugin.

Loaded ONLY by an explicit ``-p tests.mutation.plugin`` on the command line: it
is not registered in pyproject.toml and there is no conftest.py in this package,
so an ordinary ``pytest`` run is byte-identically unaffected. That is the whole
isolation story (spec §5 §3.1).

Applies the one mutant named by ``NOEMA_MUTANT`` (``none`` = baseline) and
appends one JSON row per test report to ``NOEMA_MUTANT_LOG``.
"""

import json
import os
import pathlib

from tests.mutation.mutants import MUTANTS

MUTANT = os.environ.get("NOEMA_MUTANT", "none")
OUT = pathlib.Path(os.environ.get("NOEMA_MUTANT_LOG", "mutation-runs.jsonl")).resolve()


def pytest_configure(config):
    if MUTANT != "none":
        MUTANTS[MUTANT]()  # KeyError on a typo — fail loud, never silently clean


def pytest_runtest_logreport(report):
    # Record `call` always, plus setup/teardown when they are not passing: a
    # mutant can break a teardown (checkpoint save, store cleanup) while `call`
    # still reads passed, and a setup error means the test never ran.
    if report.when != "call" and report.outcome == "passed":
        return
    with OUT.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "mutant": MUTANT,
                    "nodeid": report.nodeid,
                    "when": report.when,
                    "outcome": report.outcome,
                }
            )
            + "\n"
        )
