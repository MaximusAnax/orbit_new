"""FR-10 — phase tagging: middlegame and endgame boundaries."""

from __future__ import annotations

import chess
from chessmentor.constants import ENDGAME_MAX_PIECES, MIDDLEGAME_FULLMOVE
from chessmentor.engine.phase import (
    compute_boundaries,
    developed_minors,
    heavy_piece_count,
    phase_of,
    undeveloped_minors,
)
from chessmentor.models import Phase

ITALIAN = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"]


def test_fr10_developed_and_undeveloped_minors_partition_the_board() -> None:
    board = chess.Board()
    for color in (chess.WHITE, chess.BLACK):
        assert developed_minors(board, color) == 0
        assert undeveloped_minors(board, color) == 4
    board.push_san("Nf3")
    assert developed_minors(board, chess.WHITE) == 1
    assert undeveloped_minors(board, chess.WHITE) == 3


def test_fr10_heavy_piece_count_ignores_pawns_and_kings() -> None:
    assert heavy_piece_count(chess.Board()) == 14
    assert heavy_piece_count(chess.Board("4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w - - 0 1")) == 0
    assert heavy_piece_count(chess.Board("r3k3/8/8/8/8/8/8/4K2R w - - 0 1")) == 2


def _trade_down(max_plies: int = 120, seed: int = 20260731) -> list[str]:
    """A seeded capture-hungry line: highest-value capture, else a seeded quiet move.

    Legal by construction and reproducible, which is what a boundary property
    test needs — the ground truth is recomputed from the position, not asserted
    from a hand-typed move list.
    """
    import random

    from chessmentor.engine.evaluate import PIECE_VALUE

    rng = random.Random(seed)
    board = chess.Board()
    moves: list[str] = []
    while len(moves) < max_plies and not board.is_game_over(claim_draw=True):
        legal = sorted(board.legal_moves, key=lambda m: m.uci())
        captures = [m for m in legal if board.is_capture(m)]
        if captures:

            def value(move: chess.Move) -> tuple[int, str]:
                victim = board.piece_type_at(move.to_square)
                return (-(PIECE_VALUE[victim] if victim else PIECE_VALUE[chess.PAWN]), move.uci())

            chosen = min(captures, key=value)
        else:
            chosen = legal[rng.randrange(len(legal))]
        moves.append(chosen.uci())
        board.push(chosen)
    return moves


def test_fr10_middlegame_needs_to_be_out_of_book() -> None:
    """The book prefix is never labelled middlegame, however developed it is."""
    moves = [*ITALIAN, "e1g1", "f8c5", "d2d3", "e8g8"]
    shallow = compute_boundaries(moves, book_depth=0)
    deep = compute_boundaries(moves, book_depth=8)
    assert shallow.mg_start_ply == 6
    assert deep.mg_start_ply == 9


def test_fr10_middlegame_starts_when_both_sides_have_two_minors_out() -> None:
    moves = [*ITALIAN, "e1g1"]
    boundaries = compute_boundaries(moves, book_depth=0)
    # After ply 6 (…Nf6) White has Nf3+Bc4 and Black has Nc6+Nf6.
    assert boundaries.mg_start_ply == 6


def test_fr10_fullmove_rule_triggers_without_development() -> None:
    """Shuffling rooks: no minor leaves home, so fullmove >= 10 must fire.

    ``fullmove`` after ply p is ``1 + p // 2``, so the rule fires exactly at
    ply 18 — the first ply whose position has fullmove 10.
    """
    moves = ["a2a3", "a7a6"] + ["a1a2", "a8a7", "a2a1", "a7a8"] * 5
    boundaries = compute_boundaries(moves, book_depth=0)
    assert boundaries.mg_start_ply == 18
    replay = chess.Board()
    for uci in moves[: boundaries.mg_start_ply]:
        replay.push(chess.Move.from_uci(uci))
    assert replay.fullmove_number == MIDDLEGAME_FULLMOVE
    assert developed_minors(replay, chess.WHITE) == 0
    assert developed_minors(replay, chess.BLACK) == 0


def test_fr10_endgame_starts_when_six_or_fewer_pieces_remain() -> None:
    """The boundary is exactly the first ply crossing the Divider threshold."""
    moves = _trade_down()
    boundaries = compute_boundaries(moves, book_depth=0)
    assert boundaries.mg_start_ply is not None

    truth: int | None = None
    replay = chess.Board()
    for ply, uci in enumerate(moves, start=1):
        replay.push(chess.Move.from_uci(uci))
        if (
            truth is None
            and ply >= boundaries.mg_start_ply
            and heavy_piece_count(replay) <= ENDGAME_MAX_PIECES
        ):
            truth = ply
    assert truth is not None, "the trade-down line must reach an endgame"
    assert boundaries.eg_start_ply == truth


def test_fr10_endgame_is_reached_in_a_bare_rook_ending() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    assert heavy_piece_count(board) == 1


def test_fr10_endgame_never_precedes_middlegame() -> None:
    boundaries = compute_boundaries(ITALIAN, book_depth=0)
    if boundaries.eg_start_ply is not None:
        assert boundaries.mg_start_ply is not None
        assert boundaries.eg_start_ply >= boundaries.mg_start_ply


def test_fr10_phase_of_partitions_the_plies() -> None:
    assert phase_of(1, None, None) is Phase.OPENING
    assert phase_of(5, 7, None) is Phase.OPENING
    assert phase_of(7, 7, None) is Phase.MIDDLEGAME
    assert phase_of(20, 7, 25) is Phase.MIDDLEGAME
    assert phase_of(25, 7, 25) is Phase.ENDGAME
    assert phase_of(40, 7, 25) is Phase.ENDGAME


def test_fr10_short_game_has_no_boundaries() -> None:
    boundaries = compute_boundaries(["e2e4", "e7e5"], book_depth=4)
    assert boundaries.mg_start_ply is None
    assert boundaries.eg_start_ply is None
    assert boundaries.phase_at(1) is Phase.OPENING


def test_fr10_boundaries_are_deterministic() -> None:
    first = compute_boundaries(ITALIAN, book_depth=4)
    second = compute_boundaries(ITALIAN, book_depth=4)
    assert first == second
