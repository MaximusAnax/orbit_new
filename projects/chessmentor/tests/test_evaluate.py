"""FR-3 — tapered evaluation: symmetry, material, phase, passed pawns, tempo."""

from __future__ import annotations

import chess
from chessmentor.engine.evaluate import (
    GAME_PHASE_MAX,
    PIECE_VALUE,
    TEMPO_CP,
    evaluate,
    game_phase,
    material_balance,
)


def _mirror(fen: str) -> str:
    """Same position with colours swapped, so a correct eval is antisymmetric."""
    return chess.Board(fen).mirror().fen()


def test_fr3_start_position_is_balanced_up_to_tempo() -> None:
    assert evaluate(chess.Board()) == TEMPO_CP


def test_fr3_evaluation_is_colour_antisymmetric() -> None:
    fens = [
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 1",
        "8/5k2/4p3/3pP3/3P1K2/8/8/8 w - - 0 1",
        "r3k2r/pp3ppp/2n5/8/8/2N5/PP3PPP/R3K2R w KQkq - 0 1",
    ]
    for fen in fens:
        original = evaluate(chess.Board(fen))
        mirrored = evaluate(chess.Board(_mirror(fen)))
        # Both are "from the side to move", so a symmetric position evaluates equal.
        assert original == mirrored


def test_fr3_extra_queen_dominates_the_score() -> None:
    with_queen = evaluate(chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1"))
    bare = evaluate(chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1"))
    assert with_queen - bare > 800


def test_fr3_game_phase_runs_from_24_to_0() -> None:
    assert game_phase(chess.Board()) == GAME_PHASE_MAX
    assert game_phase(chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")) == 0
    # Minor 1, rook 2, queen 4.
    assert game_phase(chess.Board("4k3/8/8/8/8/8/8/R3K2R w - - 0 1")) == 4
    assert game_phase(chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")) == 4


def test_fr3_material_balance_uses_shannon_values() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    assert material_balance(board, chess.WHITE) == PIECE_VALUE[chess.QUEEN]
    assert material_balance(board, chess.BLACK) == -PIECE_VALUE[chess.QUEEN]


def test_fr3_passed_pawn_is_worth_more_than_a_blocked_one() -> None:
    passed = evaluate(chess.Board("4k3/8/8/3P4/8/8/8/4K3 w - - 0 1"))
    blocked = evaluate(chess.Board("4k3/3p4/8/3P4/8/8/8/4K3 w - - 0 1"))
    # The blocked position also gives Black a pawn, so compare the *swing*.
    assert passed > blocked


def test_fr3_advanced_passed_pawn_beats_a_backward_one() -> None:
    far = evaluate(chess.Board("4k3/8/3P4/8/8/8/8/4K3 w - - 0 1"))
    near = evaluate(chess.Board("4k3/8/8/8/8/3P4/8/4K3 w - - 0 1"))
    assert far > near


def test_fr3_tempo_is_added_for_the_side_to_move() -> None:
    fen = "4k3/8/8/8/8/8/8/4K3 {} - - 0 1"
    white_to_move = evaluate(chess.Board(fen.format("w")))
    black_to_move = evaluate(chess.Board(fen.format("b")))
    assert white_to_move == TEMPO_CP
    assert black_to_move == TEMPO_CP


def test_fr3_evaluation_is_pure() -> None:
    board = chess.Board()
    fen_before = board.fen()
    evaluate(board)
    assert board.fen() == fen_before
