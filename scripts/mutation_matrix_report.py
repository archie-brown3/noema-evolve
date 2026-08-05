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
# Pinned to the CURRENT run, not to a generic name: a stale artifacts/mutation/
# runs.jsonl from an earlier run sat on disk (gitignored, so invisible in review)
# and would have been preferred silently. Bump both when a new run supersedes.
RUNS = ROOT / "artifacts" / "mutation" / "runs-4.jsonl"
RUNS_GZ = ROOT / "artifacts" / "mutation" / "runs-4.jsonl.gz"
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
    # test_out_of_range_scope_raises_indexerror was deleted by the Stage 6
    # reduction: it killed nothing, and the behaviour it claimed is covered by
    # the green adapter-routed donor test_invalid_island_index_handling. Its
    # exclusion entry goes with it — the no-mutant class is 7 → 6.
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


import gzip

def load(path_or_handle):
    """-> {mutant: {nodeid: passed_bool}}, failing if ANY report for the node failed.

    Accept either a pathlib.Path (opened in text mode) or an already-open text
    file-like handle (e.g., gzip.open(..., "rt")). Folding uses logical AND so
    any failing report marks the node as failed.
    """
    outcomes = collections.defaultdict(dict)
    if isinstance(path_or_handle, pathlib.Path):
        fh = path_or_handle.open()
    else:
        fh = path_or_handle
    with fh:
        for line in fh:
            row = json.loads(line)
            passed = row["outcome"] == "passed"
            key, node = row["mutant"], row["nodeid"]
            outcomes[key][node] = outcomes[key].get(node, True) and passed
    return outcomes


def reject_incomplete(outcomes, mutants, rows):
    """Hard-fail a truncated log: every baseline-green row needs an outcome.

    A mutant run that timed out or aborted leaves the tests it never reached
    with no row at all. Treating those as passing (the old ``.get(n, True)``)
    fails open — it scores the missing tests as "not killed by this mutant",
    which can still publish a clean Rule 1 / Rule 2 verdict off a partial log.
    An unscored test is not evidence; reject the run instead.
    """
    gaps = {m: sorted(set(rows) - set(outcomes[m])) for m in mutants}
    gaps = {m: missing for m, missing in gaps.items() if missing}
    if gaps:
        detail = "; ".join(
            f"{m} ({len(missing)} missing, e.g. {missing[0]})"
            for m, missing in sorted(gaps.items())
        )
        sys.exit(
            f"incomplete run — {len(gaps)} mutant(s) have no recorded outcome for "
            f"some baseline-green test (timed out or aborted mid-run): {detail}. "
            "Re-run scripts/mutation_matrix.sh; a partial log cannot score "
            "Rule 1 or Rule 2."
        )


def main():
    if RUNS.exists():
        print(f"using raw runs {RUNS}")
        outcomes = load(RUNS)
    elif RUNS_GZ.exists():
        print(f"using compressed runs {RUNS_GZ}")
        fh = gzip.open(RUNS_GZ, mode="rt", encoding="utf-8")
        outcomes = load(fh)
    else:
        sys.exit(f"no raw runs at {RUNS} or compressed {RUNS_GZ} — run scripts/mutation_matrix.sh first")
    if "none" not in outcomes:
        sys.exit("no baseline run (NOEMA_MUTANT=none) in the raw log — matrix is unscoreable")

    baseline = outcomes["none"]
    mutants = sorted(k for k in outcomes if k != "none")
    rows = sorted(n for n, ok in baseline.items() if ok)  # baseline-green only
    population = [n for n in rows if in_population(n)]

    reject_incomplete(outcomes, mutants, rows)

    # killed[mutant] = set of rows that passed at baseline and fail under it.
    # Direct indexing, no default: reject_incomplete has already proved every
    # cell exists, and a KeyError here beats silently scoring a gap as a pass.
    killed = {m: {n for n in rows if not outcomes[m][n]} for m in mutants}

    DOCS.mkdir(parents=True, exist_ok=True)
    with (DOCS / "mutation-matrix.csv").open("w", newline="") as handle:
        # lineterminator="\n": csv defaults to "\r\n", which makes the committed
        # artifact differ from a fresh regeneration on every line and hides real
        # diffs in the noise. The matrix is dissertation evidence — regenerating
        # it must be a no-op when nothing changed.
        writer = csv.writer(handle, lineterminator="\n")
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
        "# 0188 two-way mutation matrix — run 4, at the Stage 7 widened head",
        "",
        "<!-- GENERATED FILE — DO NOT EDIT BY HAND. Produced by "
        "scripts/mutation_matrix_report.py from artifacts/mutation/runs-4.jsonl.gz; "
        "regenerate instead of editing, or your change is silently reverted on the "
        "next run. This is dissertation evidence: the caveats below (Rule 1's "
        "literal-bar reinterpretation, the unchanged-population statement, the "
        "run-against-commit provenance) are load-bearing claims about what the "
        "matrix does and does not prove, not prose to be tightened. -->",
        "",
        f"- Reduced at commit `{sha}` (HEAD when this report was generated), branch "
        "`cursor/0186-fidelity-inventory`. This is NOT necessarily the commit the "
        "matrix executed against — the driver runs first and the artifact is committed "
        "afterwards. The run-against commit is recorded in the stage note; for run 4 "
        "it was `27df25f`, the head after the Rule 2 fills landed, so the "
        "population and catalogue measured here ARE the ones that ship.",
        "- **Supersedes run 3** as the dissertation artifact. Stage 7's wrapper "
        "widening changed BOTH axes at once, so run 3's numbers describe neither "
        "the catalogue nor the population that ships: the catalogue grew 40 -> 69 "
        "(one mutant per new public wrapper capability), and 51 donor tests that "
        "FAILED at run 3's baseline — and were therefore absent from run 3's rows "
        "entirely — are now baseline-green and in the population.",
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
