# Morpion Solitaire (4D)

Evolve a C++ move-scoring heuristic for Morpion Solitaire, played through
Google DeepMind OpenSpiel's real game engine.

## The problem

Morpion Solitaire is a single-player pencil-and-paper game. It's played on a
fixed 13x13 grid that starts with a cross-shaped pattern of filled points. A
move draws a line through 4 points in a row — horizontal, vertical, or
diagonal — where exactly 3 of those points are already filled and the new
line doesn't overlap any line already played in the same direction. Filling
in the 4th point on a good line opens up new moves elsewhere on the board.
The game ends when no legal move is left, and the score is simply how many
moves you managed to play.

The 4D variant used here was solved by exhaustive computer search in 2008:
the best possible game is 35 moves, and no game can do better. That number
isn't looked up or hardcoded anywhere in this example — the program reads it
straight from the engine (`game->MaxUtility()`) and prints it alongside its
own score, so the ceiling can never drift out of sync with the code that
measures against it.

There's no known-good starting heuristic seeded into this example. The only
evolvable code (`F_mut`, inside the `EVOLVE-BLOCK` in
`initial_program.cpp`) is:

```cpp
double score_move(const open_spiel::State& state, Action action) {
  return 0.0;
}
```

Every legal move ties, and the fixed loop around it (the `F_imm` code, which
evolution can't touch) breaks the tie by always playing the lowest-numbered
legal action. That's not a heuristic choosing the move — it's an arbitrary,
deterministic fallback, and it happens to do reasonably well (see below).

## Why C++, not Python

The other examples in this repo evolve Python. This one evolves C++ because
the game itself only exists as a C++ engine inside OpenSpiel — there's no
official Python port of the rules, and hand-porting them ourselves would risk
introducing bugs. Instead, `evaluator.py` compiles the evolved `.cpp` file
against a pre-built copy of OpenSpiel's core library and runs the resulting
binary, the same way the `tsp_tour_minimization` example in upstream
OpenEvolve compiles and runs evolved C++ programs.

## One-time setup

Before running this example, OpenSpiel's core library needs to be built once
(this fetches OpenSpiel's own dependencies from GitHub and takes several
minutes on a couple of cores — building it is a one-off cost, not something
that happens per evaluation):

```
cd examples/morpion_solitaire/.openspiel-reference/open_spiel
./install.sh
mkdir -p build && cd build
BUILD_SHARED_LIB=ON cmake ../open_spiel -DCMAKE_BUILD_TYPE=Release \
    -DOPEN_SPIEL_BUILD_WITH_PYTHON=OFF -DBUILD_SHARED_LIB=ON
make -j"$(nproc)" open_spiel
```

`.openspiel-reference/` is a vendored copy of the OpenSpiel source — it's not part of the example itself; ensure the directory is added to the repository's root `.gitignore` when present.

## Scoring

`combined_score = moves_achieved / max_utility`, so a score of 1.0 means a
proven-optimal 35-move game and 0 means the program failed to run at all.

The stub above (a constant score plus the fixed tie-break) scores **26/35 ≈
0.743** — not because it embeds any strategy, but because "play the
lowest-numbered legal move" happens to be a decent policy on this board. That
score is higher than several genuinely naive alternatives we measured while
building this example (a centre-of-board heuristic scored 20, a
deterministic pseudo-random one scored 22, always preferring the
highest-numbered move scored 24). There's no way to make the true starting
point score near zero — the game simply can't end before roughly 20 moves
are played, since play only stops once no legal move remains. Worth keeping
in mind: this means the useful scoring range for evolution is fairly
narrow (roughly 20–35), and a fair number of early mutations may look like
regressions against the 0.743 baseline even if they're reasonable ideas.

## Running

```
python evaluator.py initial_program.cpp
```

The game has no randomness anywhere in it — a fixed board, a fixed rule set,
and a fixed tie-break — so the same program always produces the same score.
No repeated runs or averaging are needed.
