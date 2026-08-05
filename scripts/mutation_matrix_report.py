#!/usr/bin/env python3
"""0188 Stage 5 — reduce the raw mutation JSONL into the citable kill matrix.

Reads ``artifacts/mutation/runs.jsonl`` (one row per test report per mutant run)
and emits:

  docs/fidelity/mutation-matrix.csv  — the full machine-checkable record
  docs/fidelity/mutation-matrix.md   — the argument: both rule verdicts and only
                                       the violating rows

Two scopes, per the stage note's "Matrix test population":
  Scope A = every collected test — the Rule 2 kill oracle.
  Scope B = the declared population — the Rule 1 row set.

Rule 2 is scored in three buckets (pinned / incidentally covered / coverage
hole); a mutant whose only killers are aggregate guard nodes is demoted out of
"pinned", because "killed by the 51-row parity counter" is not "the semantic
claim is pinned".
"""

import collections
import csv
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = ROOT / "artifacts" / "mutation" / "runs.jsonl"
DOCS = ROOT / "docs" / "fidelity"

# --- Scope B: the declared population (stage note, "Matrix test population") ---
POPULATION_PREFIXES = (
    "tests/test_noema_islands_wrapper_fidelity_spec.py::",
    "tests/test_noema_islands_fidelity_spec.py::",
    "tests/test_noema_islands_adapter_fidelity_spec.py::",
    "tests/test_noema_substrate.py::TestSubstrateDatabase::",
    "tests/test_noema_prompts.py::TestPromptAssembly::",
    "tests/test_noema_prompts.py::TestOperatorTemplatePassthrough::",
)
POPULATION_EXCLUDE = (
    # Static collection-count guard, not a semantic pin.
    "tests/test_noema_islands_adapter_fidelity_spec.py::TestDonorSuiteIsFullyCollected::",
)

# Aggregate guards: they fire on *any* shift in the donor failure set or the
# routing trace, so a kill here names no specific semantic claim.
GUARD_NODES = frozenset(
    {
        "tests/test_adapter_instrumentation.py::TestTriageLedgerParity::"
        "test_failing_donor_set_is_exactly_the_declared_deviations",
        "tests/test_adapter_instrumentation.py::TestServableSurfaceRoutesThroughStore::"
        "test_every_servable_item_served_by_recorded_store_call",
        "tests/test_noema_islands_adapter_fidelity_spec.py::TestDonorSuiteIsFullyCollected::"
        "test_every_donor_test_is_collected",
    }
)


# --- Rule 1 declared exclusions (run-1 findings, mechanism per entry) --------
# Rule 1's only resolutions are "rewrite the test" or "delete it". These nodes
# admit neither, so they are declared here rather than silently dropped from the
# population — the population itself is unchanged from what the stage note
# stated before the first run.
DONOR_ROUTED_PREFIX = (
    "tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_"
)
NO_MUTANT_IN_OPTION_C = {
    "tests/test_noema_substrate.py::TestSubstrateDatabase::test_top_programs_ordering":
        "base-class SubstrateDatabase.top_programs; spec §1c excludes the shadowed "
        "base add/top_programs from the catalogue in favour of the IslandsStore "
        "overrides, so no mutant reaches this call path",
    "tests/test_noema_substrate.py::TestSubstrateDatabase::test_fitness_uses_combined_score":
        "the only fitness mutant (database.fitness.no_feature_exclusion) is "
        "identical whenever combined_score is present — by construction, per "
        "spec §2b; a mutant that ignores combined_score is not in Option C",
    "tests/test_noema_prompts.py::TestPromptAssembly::test_empty_advice_is_byte_identical":
        "killed by spec §4a's OPTIONAL 7th mutant prompts.inject_advice."
        "always_separator, which Option C does not include",
    "tests/test_noema_prompts.py::TestPromptAssembly::test_prompt_deterministic_across_builds":
        "no Option C mutant makes the sampler non-deterministic; "
        "make_prompt_sampler.allow_stochasticity drops the GUARD, which "
        "test_stochasticity_rejected already pins",
    "tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandTopProgramsThroughWrapper::"
    "test_out_of_range_scope_raises_indexerror":
        "pinned-as-observed behaviour; killed by spec §7a's fix-shaped mutant "
        "islands.top_programs.wrap_scope, which Option C does not include",
    "tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::"
    "test_stock_has_no_numpy_or_program_metadata_side_effects":
        "an absence claim (no numpy/metadata side effects); every Option C mutant "
        "is a wrong RETURN VALUE, none introduces a side effect",
    "tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::"
    "test_omitted_selection_and_old_database_config_mean_stock":
        "a differential test between two IslandsStore instances — a class-level "
        "mutant changes BOTH sides identically, so no mutant of this shape can "
        "ever be detected by it",
}


def rule1_exclusion(nodeid):
    if nodeid.startswith(DONOR_ROUTED_PREFIX):
        return (
            "donor-routed: the byte-identical donor body cannot be rewritten or "
            "deleted without destroying the instrument (spec §5 §3.0)"
        )
    return NO_MUTANT_IN_OPTION_C.get(nodeid)


def in_population(nodeid):
    if nodeid.startswith(POPULATION_EXCLUDE):
        return False
    return nodeid.startswith(POPULATION_PREFIXES)


def load(path):
    """-> {mutant: {nodeid: passed_bool}}, failing if ANY report for the node failed.

    subTest and setup/teardown emit several reports per nodeid; last-write-wins
    would silently hide a failure, so fold with AND.
    """
    outcomes = collections.defaultdict(dict)
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            passed = row["outcome"] == "passed"
            key, node = row["mutant"], row["nodeid"]
            outcomes[key][node] = outcomes[key].get(node, True) and passed
    return outcomes


def main():
    if not RUNS.exists():
        sys.exit(f"no raw runs at {RUNS} — run scripts/mutation_matrix.sh first")
    outcomes = load(RUNS)
    if "none" not in outcomes:
        sys.exit("no baseline run (NOEMA_MUTANT=none) in the raw log — matrix is unscoreable")

    baseline = outcomes["none"]
    mutants = sorted(k for k in outcomes if k != "none")
    rows = sorted(n for n, ok in baseline.items() if ok)  # baseline-green only
    population = [n for n in rows if in_population(n)]

    # killed[mutant] = set of rows that passed at baseline and fail under it
    killed = {
        m: {n for n in rows if not outcomes[m].get(n, True)} for m in mutants
    }

    DOCS.mkdir(parents=True, exist_ok=True)
    with (DOCS / "mutation-matrix.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["nodeid", "in_population"] + mutants)
        for node in rows:
            writer.writerow(
                [node, "yes" if in_population(node) else "no"]
                + ["K" if node in killed[m] else "." for m in mutants]
            )

    # Rule 1 — every Scope B test killed by >= 1 mutant, in three buckets
    unkilled = [n for n in population if not any(n in killed[m] for m in mutants)]
    placebos = [n for n in unkilled if rule1_exclusion(n) is None]
    donor_excluded = [n for n in unkilled if n.startswith(DONOR_ROUTED_PREFIX)]
    no_mutant_excluded = [
        (n, NO_MUTANT_IN_OPTION_C[n]) for n in unkilled if n in NO_MUTANT_IN_OPTION_C
    ]

    # Rule 2 — three buckets
    pinned, incidental, holes = [], [], []
    for m in mutants:
        by_population = {n for n in killed[m] if in_population(n)} - GUARD_NODES
        if by_population:
            pinned.append(m)
        elif killed[m]:
            incidental.append((m, sorted(killed[m])[:3], len(killed[m])))
        else:
            holes.append(m)

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()

    out = [
        "# 0188 Stage 5 — two-way mutation matrix",
        "",
        f"- Reduced at commit `{sha}` (HEAD when this report was generated), branch "
        "`cursor/0186-fidelity-inventory`. This is NOT necessarily the commit the "
        "matrix executed against — the driver runs first and the artifact is committed "
        "afterwards. The run-against commit is recorded in the stage note; for the "
        "final run it was `8edb181`, which differs from this one only by added docs "
        "and artifacts, no code.",
        "- Upstream pin: `openevolve` @ `80945ed` (`pyproject.toml:20`), installed at "
        "`/root/noema-evolve/.venv/lib/python3.12/site-packages/openevolve/`",
        f"- Mutants: {len(mutants)} (Option C — wrapper stores + novelty guard + PromptSampler routing)",
        f"- Matrix collection (Scope A, Rule 2 oracle): {len(baseline)} nodes, "
        f"{len(rows)} green at baseline",
        f"- Declared population (Scope B, Rule 1 rows): {len(population)} baseline-green nodes",
        "- Full per-cell record: `mutation-matrix.csv` (rows = baseline-green tests, "
        "columns = mutants, `K` = killed).",
        "",
        "## Verdicts",
        "",
        f"- **Rule 1 — every population test is killed by >=1 mutant:** "
        f"{'HOLDS where actionable' if not placebos else f'VIOLATED ({len(placebos)} placebo(s))'}"
        f" — {len(placebos)} unresolved placebo(s), "
        f"{len(donor_excluded)} donor-routed and {len(no_mutant_excluded)} no-mutant-in-scope "
        f"nodes declared below. **This is a reinterpretation of the gate's literal "
        f"bar, not a pass of it as written — the orchestrator owns that call.**",
        f"- **Rule 2 — every mutant is killed by >=1 test:** "
        f"{'HOLDS' if not holes else f'VIOLATED ({len(holes)} survivor(s))'}",
        "",
        f"Rule 2 detail: {len(pinned)} pinned (killed by a population test), "
        f"{len(incidental)} incidentally covered (killed only outside the population, "
        f"or only by an aggregate guard), {len(holes)} coverage holes.",
        "",
    ]

    out += ["## Rule 1 violations — tests killed by zero mutants, with no declared reason", ""]
    out += ["_None._", ""] if not placebos else [f"- `{n}`" for n in placebos] + [""]

    out += [
        "## Rule 1 declared exclusions — unkilled, but not placebos",
        "",
        "Rule 1's only resolutions are *rewrite* and *delete*. These nodes admit "
        "neither, so they are named here rather than removed from the population — "
        "the population is exactly what the stage note stated before the first run. "
        "They are findings about the **catalogue's reach**, which is what a two-way "
        "matrix exists to surface.",
        "",
        f"### Donor-routed ({len(donor_excluded)})",
        "",
        "Byte-identical donor bodies under `AdapterRouted_*`. Editing one destroys "
        "the instrument whose entire value is that no donor byte is edited "
        "(spec §5 §3.0). Their discriminating power is upstream's business.",
        "",
    ]
    out += [f"- `{n}`" for n in donor_excluded] or ["_None._"]
    out += ["", f"### Real claim, no mutant in Option C ({len(no_mutant_excluded)})", ""]
    out += [f"- `{n}` — {why}" for n, why in no_mutant_excluded] or ["_None._"]
    out += [""]

    out += ["## Rule 2 violations — mutants killed by zero tests", ""]
    out += ["_None._", ""] if not holes else [f"- `{m}`" for m in holes] + [""]

    out += [
        "## Incidentally covered mutants",
        "",
        "Killed, but only outside the declared population or only by an aggregate guard "
        "(triage ledger parity, servable-surface routing, donor collection count). Not a "
        "coverage hole; not evidence of a pin either.",
        "",
    ]
    if incidental:
        out += [f"- `{m}` — {n} killer(s), e.g. `{sample[0]}`" for m, sample, n in incidental]
    else:
        out += ["_None._"]
    out += [""]

    out += ["## Pinned mutants", ""]
    out += [f"- `{m}` — {len({n for n in killed[m] if in_population(n)} - GUARD_NODES)} population killer(s)" for m in pinned]
    out += [""]

    (DOCS / "mutation-matrix.md").write_text("\n".join(out))
    print(
        f"{len(rows)} baseline-green rows ({len(population)} in population) x {len(mutants)} mutants\n"
        f"Rule 1: {'HOLDS where actionable' if not placebos else f'{len(placebos)} placebo(s)'}"
        f" ({len(donor_excluded)} donor-routed + {len(no_mutant_excluded)} no-mutant declared)\n"
        f"Rule 2: {'HOLDS' if not holes else f'{len(holes)} survivor(s)'} "
        f"({len(pinned)} pinned / {len(incidental)} incidental / {len(holes)} holes)"
    )
    for name, items in (("PLACEBOS", placebos), ("SURVIVORS", holes)):
        for item in items:
            print(f"  {name}: {item}")
    for m, sample, n in incidental:
        print(f"  INCIDENTAL: {m} <- {sample[0]} (+{n - 1} more)")


if __name__ == "__main__":
    main()
