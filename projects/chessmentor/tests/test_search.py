"""FR-2 — search: determinism, node budgets, mate encoding, table lifetime."""

from __future__ import annotations

import chess
import pytest
from chessmentor.constants import MATE_SCORE
from chessmentor.engine.search import (
    SearchConfig,
    is_mate_score,
    mate_distance_plies,
    search,
)

STARTING = chess.STARTING_FEN
#: White mates in one with Ra1-a8.
MATE_IN_ONE = "7k/6pp/8/8/8/8/8/R6K w - - 0 1"
#: Black to move, only one legal reply (Kxh7).
FORCED = "7k/5K1P/8/8/8/8/8/8 b - - 0 1"


def test_fr2_search_is_deterministic_per_position() -> None:
    first = search(STARTING, SearchConfig(max_depth=3), 2_000)
    second = search(STARTING, SearchConfig(max_depth=3), 2_000)
    assert first.best_move == second.best_move
    assert first.root_scores == second.root_scores
    assert first.nodes == second.nodes
    assert first.depth == second.depth


def test_fr2_per_position_contract_survives_an_unrelated_search() -> None:
    """Tables are created empty per call, so history cannot leak between calls."""
    baseline = search(STARTING, SearchConfig(max_depth=3), 2_000)
    search(
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 1",
        SearchConfig(max_depth=3),
        3_000,
    )
    repeat = search(STARTING, SearchConfig(max_depth=3), 2_000)
    assert repeat.best_move == baseline.best_move
    assert repeat.root_scores == baseline.root_scores
    assert repeat.nodes == baseline.nodes


def test_fr2_node_budget_is_never_exceeded() -> None:
    for budget in (50, 200, 1_000, 4_000):
        result = search(STARTING, SearchConfig(max_depth=5), budget)
        assert result.nodes <= budget


def test_fr2_every_root_move_is_scored() -> None:
    board = chess.Board(STARTING)
    result = search(board, SearchConfig(max_depth=2), 5_000)
    assert set(result.root_scores) == {m.uci() for m in board.legal_moves}


def test_fr2_mate_scores_encode_distance() -> None:
    result = search(MATE_IN_ONE, SearchConfig(max_depth=3), 5_000)
    assert result.best_move is not None
    assert result.best_move.uci() == "a1a8"
    assert is_mate_score(result.best_score_cp)
    assert result.best_score_cp == MATE_SCORE - 1
    assert mate_distance_plies(result.best_score_cp) == 1


def test_fr2_shorter_mates_score_higher() -> None:
    quick = MATE_SCORE - 1
    slow = MATE_SCORE - 5
    assert quick > slow
    assert mate_distance_plies(quick) < mate_distance_plies(slow)


def test_fr2_terminal_position_returns_no_move() -> None:
    checkmated = "7k/5Q1K/8/8/8/8/8/8 b - - 0 1"
    result = search(checkmated, SearchConfig(max_depth=3), 1_000)
    assert result.best_move is None
    assert result.best_score_cp == -MATE_SCORE
    assert result.root_scores == {}


def test_fr2_single_legal_move_is_found() -> None:
    board = chess.Board(FORCED)
    assert len(list(board.legal_moves)) == 1
    result = search(board, SearchConfig(max_depth=3), 1_000)
    assert result.best_move == next(iter(board.legal_moves))


def test_fr2_pv_is_a_legal_line() -> None:
    board = chess.Board(STARTING)
    result = search(board, SearchConfig(max_depth=3), 4_000)
    probe = chess.Board(STARTING)
    assert result.pv
    for move in result.pv:
        assert move in probe.legal_moves
        probe.push(move)


def test_fr2_depth_reports_the_last_completed_iteration() -> None:
    small = search(STARTING, SearchConfig(max_depth=5), 300)
    large = search(STARTING, SearchConfig(max_depth=5), 20_000)
    assert small.depth <= large.depth
    assert large.depth >= 3


def test_fr2_search_rejects_a_zero_budget() -> None:
    with pytest.raises(ValueError, match="node_budget"):
        search(STARTING, SearchConfig(max_depth=2), 0)


def test_fr2_search_config_rejects_zero_depth() -> None:
    with pytest.raises(ValueError, match="max_depth"):
        SearchConfig(max_depth=0)


def test_fr2_move_history_does_not_change_the_result() -> None:
    """The determinism contract is per position: the move stack is discarded."""
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 1"
    from_fen = search(fen, SearchConfig(max_depth=2), 2_000)

    played = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4"):
        played.push(chess.Move.from_uci(uci))
    from_board = search(played, SearchConfig(max_depth=2), 2_000)
    assert from_board.root_scores == from_fen.root_scores
    assert from_board.nodes == from_fen.nodes


def test_fr2_finds_a_free_queen() -> None:
    """Quiescence plus move ordering must see a hanging queen at depth 1."""
    board = chess.Board("4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1")
    result = search(board, SearchConfig(max_depth=2), 3_000)
    assert result.best_move is not None
    assert result.best_move.uci() == "e4d5"
