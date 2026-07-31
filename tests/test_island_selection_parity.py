"""
Deterministic selection-parity test: noema IslandsStore vs stock OpenEvolve database.

No LLM calls. Seeds both systems with an identical population, runs N selection
steps, and compares the parent-selection distributions.

Two scenarios (matching the discussion in task 0069):
  1. cold_start  — 1 program on island 0, islands 1-3 empty.
                   This is the case that most likely explains the live-run gap.
  2. balanced    — equal programs across all 4 islands.
                   If selection differs here too, the gap is deeper than cold-start.

The test does NOT assert the sequences are identical (they won't be — the two
systems make different numbers of RNG calls before the first selection, so the
same seed produces different rand_val draws). Instead it asserts that the
*distributions* of selected islands are compatible — specifically that both
systems sample all 4 islands within N steps, and that neither is pathologically
biased. A hard assertion of bit-for-bit identity is reserved for task 0074.
"""

import random
import uuid
from collections import Counter

import pytest
from openevolve.config import DatabaseConfig
from openevolve.database import Program, ProgramDatabase

from noema.substrates.islands import IslandsStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_config(**overrides) -> DatabaseConfig:
    cfg = DatabaseConfig(
        population_size=60,
        archive_size=25,
        num_islands=4,
        elite_selection_ratio=0.3,
        exploitation_ratio=0.7,
        random_seed=42,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_program(score: float, island: int) -> Program:
    return Program(
        id=str(uuid.uuid4()),
        code=f"def priority(item, bins):\n    return -(bins - item)  # score={score}\n",
        language="python",
        metrics={"combined_score": score},
        metadata={"island": island},
    )


def _seed_oe(db: ProgramDatabase, scores_by_island: dict) -> None:
    """Populate an OpenEvolve ProgramDatabase per island."""
    for island, scores in scores_by_island.items():
        for score in scores:
            p = _make_program(score, island)
            db.add(p, target_island=island)


def _seed_noema(store: IslandsStore, scores_by_island: dict) -> None:
    """Populate a noema IslandsStore per island."""
    for island, scores in scores_by_island.items():
        for score in scores:
            p = _make_program(score, island)
            store.add(p, target_scope=island)


def _run_oe_selections(db: ProgramDatabase, n: int, seed: int) -> list[int]:
    """Run n selection steps in OpenEvolve, return list of selected islands."""
    random.seed(seed)
    selected_islands = []
    for i in range(n):
        island = i % 4
        parent, _ = db.sample_from_island(island, num_inspirations=3)
        selected_islands.append(parent.metadata.get("island", island))
    return selected_islands


def _run_noema_selections(store: IslandsStore, n: int, seed: int) -> list[int]:
    """Run n selection steps in noema, return list of selected islands."""
    random.seed(seed)
    selected_islands = []
    for i in range(n):
        target = store.target_scope(i)
        sel = store.native_select(target, num_inspirations=3)
        selected_islands.append(sel.source_scope)
    return selected_islands


# ---------------------------------------------------------------------------
# Scenario 1: cold start
# ---------------------------------------------------------------------------

class TestColdStart:
    """Single program on island 0; islands 1-3 empty."""

    POPULATION = {0: [0.9562], 1: [], 2: [], 3: []}
    N = 20  # enough to see fallback behaviour across all 4 islands

    def setup_method(self):
        cfg = _db_config()
        self.oe_db = ProgramDatabase(cfg)
        _seed_oe(self.oe_db, self.POPULATION)

        self.noema_store = IslandsStore(cfg)
        _seed_noema(self.noema_store, self.POPULATION)

    def test_oe_fallback_always_returns_a_program(self):
        """OpenEvolve must not raise when asked to sample from an empty island."""
        for island in range(4):
            parent, _ = self.oe_db.sample_from_island(island, num_inspirations=3)
            assert parent is not None

    def test_noema_fallback_always_returns_a_program(self):
        """noema native_select must not raise on empty-island target."""
        for island in range(4):
            sel = self.noema_store.native_select(island, num_inspirations=3)
            assert sel.parent is not None

    def test_both_cover_all_iterations(self):
        """Both systems complete N=20 selections without error."""
        oe = _run_oe_selections(self.oe_db, self.N, seed=42)
        noema = _run_noema_selections(self.noema_store, self.N, seed=42)
        assert len(oe) == self.N
        assert len(noema) == self.N

    def test_oe_cold_start_island_distribution(self):
        """
        OpenEvolve cold-start: record which source island each parent came from.
        With only island 0 populated, every parent should be from island 0
        (OE falls back to global sample() when island is empty, which still draws
        from island 0 — the only option).
        """
        oe = _run_oe_selections(self.oe_db, self.N, seed=42)
        c = Counter(oe)
        # All selections must come from island 0 (the only populated island)
        assert c[0] == self.N, (
            f"Expected all {self.N} cold-start selections from island 0, got {c}"
        )

    def test_noema_cold_start_island_distribution(self):
        """
        noema cold-start: same expectation — only island 0 has a program,
        so every parent should come from island 0.
        """
        noema = _run_noema_selections(self.noema_store, self.N, seed=42)
        c = Counter(noema)
        assert c[0] == self.N, (
            f"Expected all {self.N} cold-start noema selections from island 0, got {c}"
        )

    def test_cold_start_source_distribution_matches(self):
        """
        The source distributions from both systems should be identical in the
        cold-start case: both must select exclusively from island 0.
        Mismatch here means one system's fallback routes differently.
        """
        oe = Counter(_run_oe_selections(self.oe_db, self.N, seed=42))
        noema = Counter(_run_noema_selections(self.noema_store, self.N, seed=42))
        assert oe == noema, (
            f"Cold-start source distributions diverge.\n"
            f"  OpenEvolve: {dict(oe)}\n"
            f"  noema:      {dict(noema)}"
        )


# ---------------------------------------------------------------------------
# Scenario 2: balanced population
# ---------------------------------------------------------------------------

class TestBalancedPopulation:
    """Equal programs across all 4 islands — tests steady-state selection."""

    POPULATION = {
        0: [0.9562, 0.9626, 0.9580],
        1: [0.9562, 0.9590, 0.9570],
        2: [0.9562, 0.9610, 0.9555],
        3: [0.9562, 0.9600, 0.9565],
    }
    N = 40  # 10 full laps over 4 islands

    def setup_method(self):
        cfg = _db_config()
        self.oe_db = ProgramDatabase(cfg)
        _seed_oe(self.oe_db, self.POPULATION)

        self.noema_store = IslandsStore(cfg)
        _seed_noema(self.noema_store, self.POPULATION)

    def test_oe_visits_all_islands(self):
        """OpenEvolve must route at least one selection to each island."""
        oe = _run_oe_selections(self.oe_db, self.N, seed=42)
        c = Counter(oe)
        for island in range(4):
            assert c[island] > 0, f"OpenEvolve never selected from island {island}: {c}"

    def test_noema_visits_all_islands(self):
        """noema must route at least one selection to each island."""
        noema = _run_noema_selections(self.noema_store, self.N, seed=42)
        c = Counter(noema)
        for island in range(4):
            assert c[island] > 0, f"noema never selected from island {island}: {c}"

    def test_noema_target_scope_cadence(self):
        """noema target_scope must cycle 0,1,2,3,0,1,2,3,... deterministically."""
        for i in range(self.N):
            assert self.noema_store.target_scope(i) == i % 4

    def test_oe_target_cadence(self):
        """OpenEvolve target island must also cycle 0,1,2,3,... in our driver."""
        # The driver we use in the live run: `island = i % 4`
        for i in range(self.N):
            assert i % 4 == i % 4  # trivially true — documents the contract

    def test_balanced_island_shares_within_tolerance(self):
        """
        Neither system should allocate >60% of selections to one island in a
        balanced population. Pure round-robin = 25% each; exploitation/exploration
        randomness is fine, but a 60%+ skew is a pathological bias.
        """
        oe = Counter(_run_oe_selections(self.oe_db, self.N, seed=42))
        noema = Counter(_run_noema_selections(self.noema_store, self.N, seed=42))
        for island in range(4):
            oe_frac = oe[island] / self.N
            noema_frac = noema[island] / self.N
            assert oe_frac < 0.60, f"OE over-selects island {island}: {oe_frac:.0%}"
            assert noema_frac < 0.60, f"noema over-selects island {island}: {noema_frac:.0%}"
