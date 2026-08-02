"""FR-4 — the difficulty throttle: one code path, seeded draws, recorded metadata."""

from __future__ import annotations

import chess
import pytest
from chessmentor.constants import PLY_SEED_MULTIPLIER, UINT64_MASK
from chessmentor.engine.search import SearchConfig, search
from chessmentor.engine.throttle import choose_cpu_move, ply_seed
from chessmentor.models import Level

FORCED = "7k/5K1P/8/8/8/8/8/8 b - - 0 1"
MIDDLEGAME = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 1"


def _level(**overrides: object) -> Level:
    base = {
        "id": 5,
        "name": "T",
        "max_depth": 2,
        "node_budget": 1_500,
        "noise_sigma_cp": 0.0,
        "blunder_prob": 0.0,
        "blunder_margin_lo_cp": None,
        "blunder_margin_hi_cp": None,
        "book_plies": 0,
        "elo_internal": 1_000.0,
        "acpl_mean": 100.0,
        "acpl_std": 30.0,
        "calibration_seed": 1,
        "calibrated_at": "2026-07-31T00:00:00Z",
        "engine_version": "0.1.0",
    }
    base.update(overrides)
    return Level.model_validate(base)


def test_fr4_ply_seed_is_the_documented_substream() -> None:
    assert ply_seed(12345, 7) == (12345 ^ (7 * PLY_SEED_MULTIPLIER)) & UINT64_MASK
    assert ply_seed(0, 1) != ply_seed(0, 2)
    with pytest.raises(ValueError, match="1-based"):
        ply_seed(1, 0)


def test_fr4_step0_forced_move_costs_no_search() -> None:
    board = chess.Board(FORCED)
    choice = choose_cpu_move(board, _level(), game_seed=1, ply=1)
    assert choice.move.uci() == "h8h7"
    assert choice.meta.depth == 0
    assert choice.meta.nodes == 0
    assert choice.meta.root_moves == 1
    assert not choice.is_book


def test_fr4_step1_book_move_runs_no_search(book) -> None:
    board = chess.Board()
    choice = choose_cpu_move(board, _level(book_plies=4), game_seed=99, ply=1, book=book)
    assert choice.is_book
    assert choice.meta.nodes == 0
    assert choice.meta.depth == 0
    assert choice.move in board.legal_moves


def test_fr4_book_is_skipped_past_book_plies(book) -> None:
    board = chess.Board()
    choice = choose_cpu_move(board, _level(book_plies=0), game_seed=99, ply=1, book=book)
    assert not choice.is_book
    assert choice.meta.nodes > 0


def test_fr4_book_choice_is_seeded_and_reproducible(book) -> None:
    board = chess.Board()
    level = _level(book_plies=4)
    first = choose_cpu_move(board, level, game_seed=7, ply=1, book=book)
    second = choose_cpu_move(board, level, game_seed=7, ply=1, book=book)
    assert first.move == second.move
    # Different seeds must be able to produce a different book move.
    picks = {
        choose_cpu_move(board, level, game_seed=s, ply=1, book=book).move.uci() for s in range(40)
    }
    assert len(picks) > 1


def test_fr4_zero_noise_plays_the_root_best_move() -> None:
    board = chess.Board(MIDDLEGAME)
    choice = choose_cpu_move(board, _level(), game_seed=3, ply=9)
    assert choice.meta.score_cp == choice.meta.best_score_cp
    assert not choice.meta.noise_changed_pick


def test_fr4_noise_can_change_the_pick_and_is_recorded() -> None:
    board = chess.Board(MIDDLEGAME)
    level = _level(noise_sigma_cp=150.0, node_budget=600)
    changed = 0
    for seed in range(30):
        choice = choose_cpu_move(board, level, game_seed=seed, ply=9)
        if choice.meta.noise_changed_pick:
            changed += 1
            assert choice.meta.score_cp is not None
            assert choice.meta.best_score_cp is not None
            assert choice.meta.score_cp <= choice.meta.best_score_cp
    assert changed > 0


def test_fr4_step3_injected_blunder_sits_inside_the_margin_window() -> None:
    """M9 check (b), asserted directly on the throttle."""
    board = chess.Board(MIDDLEGAME)
    level = _level(
        blunder_prob=0.35, blunder_margin_lo_cp=50, blunder_margin_hi_cp=600, node_budget=600
    )
    injected = 0
    for seed in range(60):
        choice = choose_cpu_move(board, level, game_seed=seed, ply=9)
        if choice.meta.blunder_injected:
            injected += 1
            assert choice.meta.best_score_cp is not None
            assert choice.meta.score_cp is not None
            drop = choice.meta.best_score_cp - choice.meta.score_cp
            assert 50 <= drop <= 600
            assert not choice.meta.noise_changed_pick
    assert injected > 0


def test_fr4_cpu_meta_scores_match_an_independent_search_of_the_same_position() -> None:
    """``cpu_meta`` must report what the search really found, not a plausible story.

    M9 (throttle fidelity) and M9's supporting unit tests all read
    ``best_score_cp`` / ``score_cp`` / ``root_moves`` back out of the metadata the
    throttle itself wrote, so a throttle that skipped the injection but recorded
    convincing numbers would satisfy every one of them.  This test closes that
    hole from outside: it re-runs FR-4's single search at the same budget under
    FR-2's per-call-TT determinism guarantee and checks the recorded numbers
    against that independent score vector.
    """
    board = chess.Board(MIDDLEGAME)
    level = _level(
        blunder_prob=0.35, blunder_margin_lo_cp=50, blunder_margin_hi_cp=600, node_budget=600
    )
    reference = search(board, SearchConfig(max_depth=level.max_depth), level.node_budget)
    assert reference.best_move is not None

    seen_injected = 0
    seen_noise = 0
    for seed in range(60):
        choice = choose_cpu_move(board, level, game_seed=seed, ply=9)
        meta = choice.meta
        assert meta.root_moves == len(reference.root_scores), "root move count is fabricated"
        assert meta.best_score_cp == reference.best_score_cp, "best_score_cp is fabricated"
        assert meta.score_cp == reference.root_scores[choice.move.uci()], (
            "score_cp does not match the independent search's score for the played move"
        )
        if meta.blunder_injected:
            seen_injected += 1
            assert choice.move != reference.best_move, "an 'injection' that played the best move"
        elif not meta.noise_changed_pick:
            assert choice.move == reference.best_move, "clean pick that is not the best move"
        else:
            seen_noise += 1
    assert seen_injected > 0, "the fixture level must actually inject"


def test_fr4_blunder_prob_zero_never_rolls() -> None:
    board = chess.Board(MIDDLEGAME)
    level = _level(blunder_prob=0.0)
    for seed in range(10):
        choice = choose_cpu_move(board, level, game_seed=seed, ply=5)
        assert not choice.meta.blunder_rolled
        assert not choice.meta.blunder_injected


def test_fr4_blunder_rate_tracks_blunder_prob() -> None:
    """M9 check (a), asserted on the throttle's own die roll."""
    board = chess.Board(MIDDLEGAME)
    level = _level(
        blunder_prob=0.30, blunder_margin_lo_cp=80, blunder_margin_hi_cp=600, node_budget=120
    )
    trials = 300
    rolled = sum(
        choose_cpu_move(board, level, game_seed=seed, ply=5).meta.blunder_rolled
        for seed in range(trials)
    )
    assert abs(rolled / trials - 0.30) <= 0.08


def test_fr4_injection_skips_the_noise_draw() -> None:
    board = chess.Board(MIDDLEGAME)
    level = _level(
        blunder_prob=0.35,
        blunder_margin_lo_cp=50,
        blunder_margin_hi_cp=600,
        noise_sigma_cp=200.0,
        node_budget=600,
    )
    seen_injection = False
    for seed in range(60):
        choice = choose_cpu_move(board, level, game_seed=seed, ply=3)
        if choice.meta.blunder_injected:
            seen_injection = True
            assert not choice.meta.noise_changed_pick
    assert seen_injection


def test_fr4_choice_is_reproducible_for_the_same_seed_and_ply() -> None:
    board = chess.Board(MIDDLEGAME)
    level = _level(
        noise_sigma_cp=120.0,
        blunder_prob=0.2,
        blunder_margin_lo_cp=80,
        blunder_margin_hi_cp=600,
        node_budget=600,
    )
    first = choose_cpu_move(board, level, game_seed=4242, ply=13)
    second = choose_cpu_move(board, level, game_seed=4242, ply=13)
    assert first.move == second.move
    assert first.meta == second.meta


def test_fr4_metadata_reports_every_root_move() -> None:
    board = chess.Board(MIDDLEGAME)
    choice = choose_cpu_move(board, _level(), game_seed=1, ply=1)
    assert choice.meta.root_moves == len(list(board.legal_moves))


def test_fr4_node_budget_is_respected() -> None:
    """US-1's acceptance criterion: assert budgets, never wall-clock durations."""
    board = chess.Board(MIDDLEGAME)
    for budget in (400, 1_500, 6_000):
        level = _level(node_budget=budget, max_depth=5)
        choice = choose_cpu_move(board, level, game_seed=1, ply=1)
        assert choice.meta.nodes <= budget


def test_fr4_rejects_a_terminal_position() -> None:
    board = chess.Board("7k/5Q1K/8/8/8/8/8/8 b - - 0 1")
    with pytest.raises(ValueError, match="no legal move"):
        choose_cpu_move(board, _level(), game_seed=1, ply=1)
