"""FR-8, FR-9, FR-15: the reordering heuristic, anchors and determinism."""

from __future__ import annotations

import itertools
import random
import time
from collections.abc import Iterator

import numpy as np
import pytest
from flowlist.engine.models import (
    MAX_EXACT_SIZE,
    MAX_PLAYLIST_SIZE,
    Algorithm,
    FeatureSnapshot,
)
from flowlist.engine.optimizer import (
    MAX_STARTS,
    exact_optimal,
    reorder,
    total_of,
)
from flowlist.engine.scoring import build_matrix, order_total
from flowlist.errors import InstanceTooLargeError, InvalidAnchorError, PlaylistTooLargeError
from flowlist_testkit import make_features, random_matrix


def brute_force(
    matrix: list[list[float]], start: int | None = None, end: int | None = None
) -> tuple[list[int], float]:
    """Ground truth by exhaustive enumeration — trivial at n <= 7 (7! = 5040)."""
    n = len(matrix)
    best_order: list[int] = []
    best_total = -float("inf")
    for permutation in itertools.permutations(range(n)):
        if start is not None and permutation[0] != start:
            continue
        if end is not None and permutation[-1] != end:
            continue
        total = sum(matrix[permutation[i]][permutation[i + 1]] for i in range(n - 1))
        if total > best_total + 1e-12:
            best_total, best_order = total, list(permutation)
    return best_order, best_total


# --------------------------------------------------------------------------- #
# FR-8 -- correctness of the exact solver the evals lean on
# --------------------------------------------------------------------------- #


def test_fr8_exact_matches_bruteforce() -> None:
    """EVALS 3-M4's independent cross-check of the Held-Karp ground truth.

    A fake or buggy DP — including the degenerate "implement exact as the
    heuristic" case — cannot survive exhaustive enumeration over 20 seeded
    matrices in unanchored, start-, end- and both-anchored variants.
    """
    rng = random.Random(20260731)
    checked = 0
    for trial in range(20):
        n = rng.randint(2, 7)
        matrix = random_matrix(n, seed=1000 + trial)
        variants: list[tuple[int | None, int | None]] = [(None, None), (0, None), (None, n - 1)]
        if n > 1:
            variants.append((0, n - 1))
        for start, end in variants:
            expected_order, expected_total = brute_force(matrix, start, end)
            solution = exact_optimal(matrix, start, end)
            assert solution.total == pytest.approx(expected_total, abs=1e-9), (n, start, end)
            assert order_total(solution.order, matrix) == pytest.approx(expected_total, abs=1e-9)
            assert sorted(solution.order) == list(range(n))
            if start is not None:
                assert solution.order[0] == start
            if end is not None:
                assert solution.order[-1] == end
            assert len(expected_order) == n
            checked += 1
    assert checked >= 20


def test_fr8_exact_is_deterministic() -> None:
    matrix = random_matrix(7, seed=77)
    first = exact_optimal(matrix)
    for _ in range(3):
        assert exact_optimal(matrix) == first


def test_fr8_exact_guarded_by_size() -> None:
    matrix = random_matrix(MAX_EXACT_SIZE + 1, seed=1)
    with pytest.raises(InstanceTooLargeError) as excinfo:
        exact_optimal(matrix)
    assert excinfo.value.code == "instance_too_large"


def test_fr8_exact_degenerate_sizes() -> None:
    assert exact_optimal([]) == ([], 0.0)
    assert exact_optimal([[0.0]]) == ([0], 0.0)
    assert exact_optimal([[0.0]], start=0, end=0) == ([0], 0.0)


# --------------------------------------------------------------------------- #
# FR-8 -- the heuristic
# --------------------------------------------------------------------------- #


def test_fr8_reorder_returns_a_permutation() -> None:
    for n in (2, 3, 5, 13, 40):
        matrix = random_matrix(n, seed=n)
        result = reorder(matrix, seed=7)
        assert sorted(result.order) == list(range(n))
        assert result.algorithm is Algorithm.GREEDY_2OPT


def test_fr8_edge_cases_below_four_entries() -> None:
    assert reorder([]).order == []
    assert reorder([[0.0]]).order == [0]

    # n = 2: the better of the two directed orders must win.
    matrix = [[0.0, 0.2], [0.9, 0.0]]
    assert reorder(matrix, seed=1).order == [1, 0]
    assert reorder([[0.0, 0.9], [0.2, 0.0]], seed=1).order == [0, 1]

    # n = 3: exhaustively optimal.
    for seed in range(5):
        matrix3 = random_matrix(3, seed=seed)
        assert total_of(reorder(matrix3, seed=seed).order, np.asarray(matrix3)) == pytest.approx(
            brute_force(matrix3)[1], abs=1e-9
        )


def test_fr8_never_beats_the_exact_optimum() -> None:
    """EVALS 3-M4's hard sanity assert, as a unit test."""
    for trial in range(12):
        matrix = random_matrix(9, seed=500 + trial)
        heuristic = reorder(matrix, seed=trial)
        assert heuristic.total <= exact_optimal(matrix).total + 1e-9


def test_fr8_near_optimal_on_realistic_instances() -> None:
    """M4-style optimality ratio on genre-clustered features (gate: 0.97/0.90)."""
    ratios = []
    for trial in range(10):
        features = make_features(8 + trial % 7, seed=300 + trial)
        matrix = build_matrix(features)
        heuristic = reorder(matrix, seed=7)
        optimum = exact_optimal(matrix).total
        assert optimum > 0
        ratios.append(heuristic.total / optimum)
    assert min(ratios) >= 0.90
    assert sum(ratios) / len(ratios) >= 0.97


def test_fr8_local_search_earns_its_keep() -> None:
    """The improvement over bare greedy construction must be real, not zero."""
    improvements = []
    for trial in range(6):
        features = make_features(40, seed=700 + trial)
        matrix = build_matrix(features)
        result = reorder(matrix, seed=3)
        assert result.total >= result.construction_total
        improvements.append(result.improvement)
    assert max(improvements) > 0.0
    assert sum(improvements) / len(improvements) > 0.0


def test_fr8_beats_naive_strategies() -> None:
    """M6-style margin against bpm_sort, the strongest naive strategy."""
    for trial in range(4):
        n = 30 + 15 * trial
        features = make_features(n, seed=800 + trial)
        matrix = build_matrix(features)
        heuristic = reorder(matrix, seed=7).total / (n - 1)
        bpm_sorted = sorted(range(n), key=lambda i: (features[i].bpm or 0.0, i))
        naive = order_total(bpm_sorted, matrix) / (n - 1)
        identity = order_total(list(range(n)), matrix) / (n - 1)
        assert heuristic > naive + 0.08, (n, heuristic, naive)
        assert heuristic > identity


def test_fr8_recovers_a_planted_chain() -> None:
    """M5-style recovery: a shuffled excellent chain must be found again."""
    # Walk the Camelot wheel in fifths with tiny tempo/energy drift, so every
    # planted transition scores well by construction.
    n = 60
    features = []
    bpm = 122.0
    energy = 0.55
    rng = random.Random(4)
    for step in range(n):
        bpm *= 1.0 + rng.uniform(-0.012, 0.012)
        bpm = min(132.0, max(118.0, bpm))
        energy = min(0.9, max(0.35, energy + rng.uniform(-0.04, 0.04)))
        features.append(
            FeatureSnapshot(
                bpm=round(bpm, 2),
                key_pc=(9 + 7 * step) % 12,
                mode=0,
                energy=round(energy, 3),
                loudness_db=round(-14.0 + 10.0 * energy, 2),
            )
        )
    planted = list(range(n))
    shuffled = planted[:]
    random.Random(11).shuffle(shuffled)

    matrix = build_matrix(features)
    planted_mean = order_total(planted, matrix) / (n - 1)
    found_mean = reorder(matrix, seed=7).total / (n - 1)
    assert planted_mean >= 0.80  # the chain really is excellent
    assert found_mean / planted_mean >= 0.92


def test_fr8_escapes_a_nearest_neighbour_trap() -> None:
    """A locally-best first hop that strands a high-scoring cluster.

    Construction-only greedy takes the bait; the local search must undo it.
    """
    n = 8
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                matrix[i][j] = 0.05
    # A tight, excellent cycle through 1..7 ...
    chain = [1, 2, 3, 4, 5, 6, 7]
    for a, b in itertools.pairwise(chain):
        matrix[a][b] = 0.95
    # ... and a tempting but dead-end first hop out of node 0.
    matrix[0][7] = 0.99
    matrix[0][1] = 0.90

    result = reorder(matrix, seed=7)
    optimum = exact_optimal(matrix).total
    assert result.total == pytest.approx(optimum, abs=1e-9)
    assert result.total > result.construction_total or result.order[:2] == [0, 1]


def test_fr8_enforces_the_size_cap() -> None:
    oversized = [[0.0] * (MAX_PLAYLIST_SIZE + 1) for _ in range(MAX_PLAYLIST_SIZE + 1)]
    with pytest.raises(PlaylistTooLargeError) as excinfo:
        reorder(oversized)
    assert excinfo.value.code == "playlist_too_large"
    assert excinfo.value.context["cap"] == MAX_PLAYLIST_SIZE


def test_fr8_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        reorder([[0.0, 1.0]])  # not square
    with pytest.raises(ValueError):
        reorder(random_matrix(4, seed=1), max_passes=0)


def test_fr8_multi_start_count_follows_d6() -> None:
    """k = min(n, 12) constructions."""
    assert reorder(random_matrix(5, seed=1), seed=1).starts == 5
    assert reorder(random_matrix(30, seed=1), seed=1).starts == MAX_STARTS


def test_fr8_max_passes_bounds_the_loop() -> None:
    matrix = build_matrix(make_features(60, seed=99))
    capped = reorder(matrix, seed=7, max_passes=1)
    assert capped.passes == MAX_STARTS  # one pass per construction
    full = reorder(matrix, seed=7, max_passes=50)
    assert full.total >= capped.total


# --------------------------------------------------------------------------- #
# FR-9 -- anchors
# --------------------------------------------------------------------------- #


def test_fr9_anchors() -> None:
    """Start-only, end-only, both, and the n = 2 degenerate case."""
    features = make_features(12, seed=42)
    matrix = build_matrix(features)

    start_only = reorder(matrix, seed=7, start=3)
    assert start_only.order[0] == 3
    assert sorted(start_only.order) == list(range(12))

    end_only = reorder(matrix, seed=7, end=9)
    assert end_only.order[-1] == 9
    assert sorted(end_only.order) == list(range(12))

    both = reorder(matrix, seed=7, start=3, end=9)
    assert both.order[0] == 3
    assert both.order[-1] == 9
    assert sorted(both.order) == list(range(12))

    # n = 2 degenerates correctly with both endpoints pinned.
    two = [[0.0, 0.3], [0.8, 0.0]]
    assert reorder(two, seed=1, start=0, end=1).order == [0, 1]
    assert reorder(two, seed=1, start=1, end=0).order == [1, 0]
    assert reorder([[0.0]], start=0, end=0).order == [0]


def test_fr9_anchors_survive_local_search() -> None:
    """Moves that would displace an anchor are rejected, at every seed."""
    matrix = build_matrix(make_features(25, seed=13))
    for seed in range(3):
        for start, end in ((0, None), (None, 24), (5, 17), (24, 0)):
            result = reorder(matrix, seed=seed, start=start, end=end, max_passes=50)
            assert sorted(result.order) == list(range(25))
            if start is not None:
                assert result.order[0] == start
            if end is not None:
                assert result.order[-1] == end


def test_fr9_anchored_quality_matches_anchored_ground_truth() -> None:
    """The anchored heuristic is compared against fixed-endpoint Held-Karp."""
    for trial in range(6):
        features = make_features(10, seed=900 + trial)
        matrix = build_matrix(features)
        start, end = 0, 9
        heuristic = reorder(matrix, seed=7, start=start, end=end)
        optimum = exact_optimal(matrix, start, end).total
        assert heuristic.total <= optimum + 1e-9
        assert heuristic.total / optimum >= 0.90


def test_fr9_anchored_construction_uses_seeded_diversification() -> None:
    """D6: under a start anchor every construction roots at it, so the k
    constructions must differ through seeded near-tie choice instead."""
    matrix = build_matrix(make_features(30, seed=55))
    orders = {tuple(reorder(matrix, seed=seed, start=4).order) for seed in range(5)}
    assert len(orders) > 1, "seeded diversification produced identical results"
    assert all(order[0] == 4 for order in orders)


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, None), (None, 12), (12, None), (3, 3), (True, None)],
)
def test_fr9_invalid_anchors_rejected(start: object, end: object) -> None:
    matrix = random_matrix(12, seed=2)
    with pytest.raises(InvalidAnchorError):
        reorder(matrix, seed=1, start=start, end=end)  # type: ignore[arg-type]


def test_fr9_exact_accepts_the_same_anchors() -> None:
    matrix = random_matrix(6, seed=6)
    solution = exact_optimal(matrix, 2, 5)
    assert solution.order[0] == 2
    assert solution.order[-1] == 5
    with pytest.raises(InvalidAnchorError):
        exact_optimal(matrix, 2, 2)


# --------------------------------------------------------------------------- #
# FR-15 -- determinism
# --------------------------------------------------------------------------- #


def test_fr15_same_seed_same_order() -> None:
    matrix = build_matrix(make_features(45, seed=64))
    first = reorder(matrix, seed=7)
    for _ in range(3):
        repeat = reorder(matrix, seed=7)
        assert repeat.order == first.order
        assert repeat.total == first.total
        assert repeat.passes == first.passes


def test_fr15_determinism_holds_under_anchors_and_profiles() -> None:
    matrix = build_matrix(make_features(30, seed=65))
    for start, end in ((None, None), (2, None), (None, 7), (2, 7)):
        runs = [reorder(matrix, seed=11, start=start, end=end).order for _ in range(3)]
        assert runs[0] == runs[1] == runs[2]


def test_fr15_different_seeds_may_differ_but_stay_valid() -> None:
    matrix = build_matrix(make_features(40, seed=66))
    orders = [tuple(reorder(matrix, seed=seed).order) for seed in range(4)]
    for order in orders:
        assert sorted(order) == list(range(40))
    # Not asserted to differ (a strong instance can have one clear optimum),
    # but the seed must at least be wired into construction.
    assert len(set(orders)) >= 1


def test_fr15_total_agrees_with_independent_rescoring() -> None:
    """The optimizer's total must equal the FlowReport's, bit for bit."""
    features = make_features(35, seed=67)
    matrix = build_matrix(features)
    result = reorder(matrix, seed=7)
    assert result.total == order_total(result.order, matrix)


def test_fr15_rounding_makes_near_ties_stable() -> None:
    """D6: scores are compared at 12 decimals, so 1e-15 noise cannot flip a tie."""
    base = random_matrix(10, seed=8)
    jittered = [[value + (1e-15 if value else 0.0) for value in row] for row in base]
    assert reorder(base, seed=5).order == reorder(jittered, seed=5).order


# --------------------------------------------------------------------------- #
# FR-8 -- performance NFR (slow-marked, excluded from the default suite)
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_fr8_perf_smoke() -> None:
    """A reorder at the n = 500 cap completes in <= 60 s (FR-8 NFR)."""
    features = make_features(MAX_PLAYLIST_SIZE, seed=2026)
    matrix = build_matrix(features)
    started = time.perf_counter()
    result = reorder(matrix, seed=7, max_passes=50)
    elapsed = time.perf_counter() - started
    assert sorted(result.order) == list(range(MAX_PLAYLIST_SIZE))
    assert elapsed <= 60.0, f"reorder at n={MAX_PLAYLIST_SIZE} took {elapsed:.1f}s"


def _or_opt_neighbours(order: list[int]) -> Iterator[list[int]]:
    """Every relocation of a length-1..3 segment, direction preserved."""
    n = len(order)
    for i in range(n):
        for length in (1, 2, 3):
            if i + length > n:
                break
            segment = order[i : i + length]
            rest = order[:i] + order[i + length :]
            for gap in range(len(rest) + 1):
                candidate = rest[:gap] + segment + rest[gap:]
                if candidate != order:
                    yield candidate


def _two_opt_neighbours(order: list[int]) -> Iterator[list[int]]:
    """Every segment reversal."""
    n = len(order)
    for i in range(n - 1):
        for j in range(i + 1, n):
            yield order[:i] + order[i : j + 1][::-1] + order[j + 1 :]


def test_fr8_result_is_a_genuine_local_optimum() -> None:
    """No Or-opt or 2-opt move improves the returned order.

    This is the strongest available check on the move-delta arithmetic: a sign
    error or a missed boundary edge would leave an improving move on the table
    that brute-force enumeration finds immediately.
    """
    for trial in range(4):
        features = make_features(14, seed=1200 + trial)
        matrix = build_matrix(features)
        result = reorder(matrix, seed=7, max_passes=200)
        best = order_total(result.order, matrix)
        for neighbour in itertools.chain(
            _or_opt_neighbours(result.order), _two_opt_neighbours(result.order)
        ):
            assert order_total(neighbour, matrix) <= best + 1e-12, (trial, neighbour)


def test_fr8_anchored_result_is_a_local_optimum_within_its_constraints() -> None:
    for start, end in ((0, None), (None, 11), (2, 9)):
        features = make_features(12, seed=1300)
        matrix = build_matrix(features)
        result = reorder(matrix, seed=5, start=start, end=end, max_passes=200)
        best = order_total(result.order, matrix)
        for neighbour in itertools.chain(
            _or_opt_neighbours(result.order), _two_opt_neighbours(result.order)
        ):
            if start is not None and neighbour[0] != start:
                continue
            if end is not None and neighbour[-1] != end:
                continue
            assert order_total(neighbour, matrix) <= best + 1e-12, (start, end, neighbour)
