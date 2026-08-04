// Morpion Solitaire (4D) move-scoring heuristic, played through Google
// DeepMind OpenSpiel's real engine. Evolution changes only score_move().

#include <algorithm>
#include <iostream>
#include <memory>
#include <vector>

#include "open_spiel/games/morpion_solitaire/morpion_solitaire.h"
#include "open_spiel/spiel.h"

using open_spiel::Action;
using open_spiel::morpion_solitaire::Line;
using open_spiel::morpion_solitaire::Point;

// EVOLVE-BLOCK-START

// F_mut: score a candidate move; the highest score is played. Stub.
double score_move(const open_spiel::State& state, Action action) {
  return 0.0;
}

// EVOLVE-BLOCK-END

// F_imm: fixed driver — plays one game and reports the result. Legal actions
// are sorted and only a strictly greater score displaces the incumbent, so
// ties go to the lowest action id and the playthrough stays deterministic.
int main() {
  std::shared_ptr<const open_spiel::Game> game =
      open_spiel::LoadGame("morpion_solitaire");
  std::unique_ptr<open_spiel::State> state = game->NewInitialState();

  while (!state->IsTerminal()) {
    std::vector<Action> legal = state->LegalActions();
    if (legal.empty()) break;
    std::sort(legal.begin(), legal.end());

    Action best_action = legal[0];
    double best_score = score_move(*state, legal[0]);
    for (std::size_t i = 1; i < legal.size(); ++i) {
      double score = score_move(*state, legal[i]);
      if (score > best_score) {
        best_score = score;
        best_action = legal[i];
      }
    }
    state->ApplyAction(best_action);
  }

  // Ceiling comes from the engine, never hardcoded here or in the evaluator.
  std::cout << "MAX_UTILITY: " << game->MaxUtility() << "\n";
  std::cout << "MOVES_ACHIEVED: " << state->Returns()[0] << "\n";
  return 0;
}
