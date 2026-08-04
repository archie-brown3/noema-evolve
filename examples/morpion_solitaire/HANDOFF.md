# Morpion Solitaire OpenEvolve example — handoff spec

## Branch
`feature/morpion-solitaire-example`, based on `7bd9676` on top of
`codex/adr-noema-console-logging-openevolve`. Pre-existing uncommitted
changes on that branch (`.gitignore`, some example configs, several test
files) are carried over untouched — do not revert or clean them, they are
unrelated in-progress work, not artifacts of this task.

## Goal
Add a new OpenEvolve example, `examples/morpion_solitaire/`, alongside the
existing `circle_packing`/`bin_packing`/`tsp_tour_minimization` examples:
evolve a short C++ move-scoring heuristic for Morpion Solitaire (the 4D
variant, i.e. Google DeepMind OpenSpiel's `morpion_solitaire` game).

## Why this game (context, not re-litigable — settled after a long design
discussion this session)
- Single-player, fully deterministic (fixed 13x13 starting board, zero
  randomness anywhere in the rules) — no seeding/averaging needed, one
  playthrough per evaluation is enough.
- Has a **proven-optimal** score ceiling of 35 moves (exhaustively enumerated
  in 2008, cited in OpenSpiel's own source as `http://oeis.org/A204109`),
  exposed by the engine itself (`MaxUtility()` in C++ / `game.max_utility()`
  in Python) — not something we hardcode or look up externally.
- This ceiling is a **benchmark number**, not a stored solution: nobody
  needs the actual 35-move sequence to grade a candidate heuristic, only the
  number. Compare: `circle_packing`'s evaluator uses `TARGET_VALUE = 2.635`
  (AlphaEvolve's *best-known*, not proven-optimal) the exact same way — as a
  bare normalization constant, never as embedded solution content. Same
  pattern here, just with a stronger (proven, not best-known) guarantee.
- OpenSpiel's own code contains **no heuristic, solver, or reference agent**
  for this game — only the rules engine (legal moves, apply, terminal). The
  only Python-side usage anywhere in OpenSpiel is a generic *random*-move
  example loop. So there is nothing to "leak" from the engine into the
  evolved program.

## Architecture decision: real OpenSpiel C++ engine + C++ evolved program
(This reverses an earlier direction in this session where a pure-Python
reimplementation of the rules was planned to avoid a C++ build. That was
wrong — see below.)

**Follow the pattern already used by
`.openevolve-upstream/examples/tsp_tour_minimization/`** (a full,
production-grade C++ OpenEvolve example) and
`.openevolve-upstream/examples/rust_adaptive_sort/` (same idea, Rust): the
evolved program (`initial_program.cpp`) is native C++, compiled fresh each
evaluation; `evaluator.py` is still plain Python (a hard OpenEvolve
requirement — the framework always calls a Python `evaluate()`), but its job
is just to shell out to a compiler and run the resulting binary, exactly like
`tsp_tour_minimization/utils/tsp_runner.py:compile_tsp_executable()`:

```python
cmd = [
    "g++", "-std=gnu++17", "-O3", "-DNDEBUG", "-march=native",
    "-funroll-loops", "-ffast-math",
    "-I", "include", "TSP.cpp", "-o", output_bin_path,
]
# + platform-specific link flags (-lpthread -lm [-ldl on Linux])
subprocess.Popen(cmd, cwd=dir_path, stdout=PIPE, stderr=PIPE, ...)
```

Reasoning for choosing this over the Python-port alternative:
- Uses OpenSpiel's actual, verified engine directly — zero reimplementation
  risk, in contrast to hand-porting the rules to Python (which was the
  earlier, now-abandoned plan).
- Real, detailed precedent exists in this exact framework for compiled,
  non-Python evolved programs (both `tsp_tour_minimization` and
  `rust_adaptive_sort`), so this is not a novel pattern for OpenEvolve.
- The C++ build cost (OpenSpiel's core library) is a **one-time** setup
  cost, not paid per evaluation — each generation only recompiles the small
  evolved `.cpp` file and links against the already-built OpenSpiel objects.

Known tradeoff, accepted: LLM-driven mutation is generally less reliable on
C++ than Python (compile errors / memory-safety mistakes cost more
iterations) — noted, not a blocker, matches how `tsp_tour_minimization`
already operates in production.

## What's already done this session
- Branch `feature/morpion-solitaire-example` created and checked out.
- `examples/morpion_solitaire/.openspiel-reference/` — shallow clone of
  `google-deepmind/open_spiel` (reference source, read-only, gitignored via
  `.openspiel-reference/` entry added to root `.gitignore`). Use this to
  look up exact source, don't re-clone.
- Read and fully understood `morpion_solitaire.h`/`.cc` (board layout, 460
  possible lines, action encoding scheme, legality rule, apply/terminal
  logic) — see "Game facts" below, no need to re-derive.
- Read `circle_packing/evaluator.py` in full (the subprocess+timeout
  pattern other Python-evolved-program examples use) and
  `tsp_tour_minimization/utils/tsp_runner.py`'s
  `compile_tsp_executable()` (the compile-then-run pattern to follow here).

## Remaining steps

1. **Build OpenSpiel's core library once.** In
   `examples/morpion_solitaire/.openspiel-reference/`, this needs
   `open_spiel/scripts/install.sh` (fetches abseil-cpp into
   `open_spiel/abseil-cpp/` via `git clone`, per
   `install.sh:134-136`, pinned version `OPEN_SPIEL_ABSL_VERSION`) followed
   by the standard CMake configure+build targeting the `open_spiel` SHARED
   library target (`open_spiel/CMakeLists.txt:318-324`) — not the full test
   suite or Python bindings, just that target, to keep the build minimal.
   This step was interrupted mid-investigation last session; `cmake`,
   `clang++`, and `g++` are all confirmed present on this machine already.
   Expect this to take several minutes (network fetch + native compile).
   Run it in the background / with a generous timeout.

2. **New example directory** `examples/morpion_solitaire/`:
   - `initial_program.cpp` — includes OpenSpiel's
     `open_spiel/games/morpion_solitaire/morpion_solitaire.h` and drives the
     game via its C++ API directly (`MorpionGame`/`MorpionState` —
     `NewInitialState()`, `LegalActions()`, `ApplyAction()`, `IsTerminal()`,
     `Returns()`), same generic loop shape as OpenSpiel's own
     `python/examples/example.py` but choosing moves via a
     `score_move(...)` function instead of randomly. Only `score_move`
     lives inside the `EVOLVE-BLOCK-START`/`END` markers (per
     `examples/README.md`'s hard constraint: exactly one EVOLVE-BLOCK); the
     driving loop is fixed/immutable code outside it, following the
     `F_imm`/`F_mut` comment-tag convention used in `circle_packing` and
     `bin_packing`. Stub `score_move` starts trivial (e.g. returns a
     constant) — do not seed it with anything resembling a good heuristic
     or a known solution.
   - `evaluator.py` — Python, following `compile_tsp_executable()`'s
     pattern: compile `initial_program.cpp` against the prebuilt OpenSpiel
     objects from step 1 (link flags needed: whatever `open_spiel`'s CMake
     build reports — check `open_spiel/CMakeLists.txt` link_libraries
     around line 209, plus abseil libs), run the resulting binary with a
     timeout (reuse `circle_packing/evaluator.py`'s subprocess/timeout
     shape for the "kill on timeout, never let an exception propagate,
     always return `combined_score` even on failure" behavior), parse its
     stdout for the moves-achieved count, and compute
     `combined_score = moves_achieved / 35.0`. Read the `35` from the
     compiled program's own output (have `initial_program.cpp` print
     `game->MaxUtility()`, not a hardcoded Python constant) so the ceiling
     is never duplicated/hardcoded in two places.
   - `config.yaml` — minimal, modeled on
     `bin_packing/config_phase_1.yaml`'s structure but using this fork's
     schema (`agent`/`coordination`/`substrate` sections — check a sibling
     example's `config.yaml` in this fork's `examples/`, not the generic
     upstream template) with `language: "cpp"` (or `"mixed"`, matching
     `tsp_tour_minimization/config.yaml:5`) and `file_suffix: .cpp`.
   - No `requirements.txt` needed (no Python third-party dependency; the
     C++ dependency is the prebuilt OpenSpiel objects from step 1, not a
     pip package).

3. **Verification**:
   - Compile+run `initial_program.cpp` directly (outside the evaluator) once
     to confirm it links against OpenSpiel correctly and prints a plausible
     move count with the stub heuristic.
   - Run `evaluator.py` against the stub program: confirm it returns
     `combined_score` near 0 (stub is deliberately bad), no exceptions, and
     two consecutive runs produce identical `moves_achieved` (determinism
     check — the game and a fixed heuristic have zero randomness, so this
     must hold exactly).

## Game facts (ground truth, already verified against
`.openspiel-reference/open_spiel/games/morpion_solitaire/{morpion_solitaire.h,.cc}`
this session — no need to re-derive)

- Board: 13x13 grid (169 points), fixed starting cross shape (see
  `morpion_solitaire.cc:229-244` for the exact filled-point rule), no
  parameters, `NumPlayers() = 1`, `ChanceMode::kDeterministic`.
- 460 total possible 4-point lines (`NumDistinctActions() = 460`), 4
  directions: `[0,1]` horizontal, `[1,0]` vertical, `[1,1]`/`[1,-1]`
  diagonals, actions partitioned into ranges `0-129`, `130-259`, `260-359`,
  `360-459` respectively (exact encoding in `morpion_solitaire.cc:57-93,
  144-177` — don't need to reproduce this by hand, just call the real
  `Line`/action API since we're using the actual engine now).
- A move is legal iff exactly 3 of its 4 points are filled AND it doesn't
  overlap (share a point, same direction) any previously-played line
  (`morpion_solitaire.cc:268-294`).
- `Returns()` = total moves played so far (`current_returns_`,
  incremented by 1.0 per move). `MaxUtility() = MaxGameLength() = 35`,
  proven optimal (not merely best-known).
- No solution/move-sequence exists anywhere in the OpenSpiel codebase —
  confirmed by direct source read, not assumption.

## Explicitly out of scope for this pass
- Tuning `config.yaml` for real evolution runs (population size, LLM model
  choice, etc.) — minimal only.
- README.md for the example.
- Naive-greedy or random-play baseline scripts (useful later for reporting
  in the dissertation/write-up, not needed to get the evaluator working).
- Any other game/domain — this handoff is scoped to Morpion Solitaire only.
