"""FR-10 — deterministic phase tagging.

``middlegame_start`` = the first ply where the position is out of book AND
(each side has >= 2 minor pieces off their home squares OR fullmove >= 10).

``endgame_start`` = the first ply >= ``middlegame_start`` where the total count
of non-pawn, non-king pieces (both sides) is <= 6; absent if never reached.

The endgame threshold follows the Lichess "Divider" piece-count idea; the
middlegame rule is ours — a book-aware trainer knows exactly when theory ended,
which is the more meaningful boundary for coaching (D13).

All thresholds are named constants, so they are data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import chess

from ..constants import (
    ENDGAME_MAX_PIECES,
    MIDDLEGAME_FULLMOVE,
    MIDDLEGAME_MIN_DEVELOPED_MINORS,
)
from ..models import Phase

__all__ = [
    "MINOR_HOME_SQUARES",
    "PhaseBoundaries",
    "compute_boundaries",
    "developed_minors",
    "heavy_piece_count",
    "phase_of",
    "undeveloped_minors",
]

#: Where each side's knights and bishops start.
MINOR_HOME_SQUARES: dict[bool, frozenset[int]] = {
    chess.WHITE: frozenset({chess.B1, chess.G1, chess.C1, chess.F1}),
    chess.BLACK: frozenset({chess.B8, chess.G8, chess.C8, chess.F8}),
}


@dataclass(frozen=True)
class PhaseBoundaries:
    """The two FR-10 boundaries for one game."""

    mg_start_ply: int | None
    eg_start_ply: int | None

    def phase_at(self, ply: int) -> Phase:
        return phase_of(ply, self.mg_start_ply, self.eg_start_ply)


def developed_minors(board: chess.Board, color: chess.Color) -> int:
    """``color``'s knights and bishops standing off their home squares."""
    home = MINOR_HOME_SQUARES[color]
    count = 0
    for piece_type in (chess.KNIGHT, chess.BISHOP):
        for square in board.pieces(piece_type, color):
            if square not in home:
                count += 1
    return count


def undeveloped_minors(board: chess.Board, color: chess.Color) -> int:
    """``color``'s knights and bishops still sitting on their home squares."""
    home = MINOR_HOME_SQUARES[color]
    count = 0
    for piece_type in (chess.KNIGHT, chess.BISHOP):
        for square in board.pieces(piece_type, color):
            if square in home:
                count += 1
    return count


def heavy_piece_count(board: chess.Board) -> int:
    """Non-pawn, non-king pieces on the board, both sides (Lichess Divider)."""
    return sum(
        len(board.pieces(piece_type, color))
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for color in (chess.WHITE, chess.BLACK)
    )


def _is_middlegame_position(board: chess.Board) -> bool:
    both_developed = (
        developed_minors(board, chess.WHITE) >= MIDDLEGAME_MIN_DEVELOPED_MINORS
        and developed_minors(board, chess.BLACK) >= MIDDLEGAME_MIN_DEVELOPED_MINORS
    )
    return both_developed or board.fullmove_number >= MIDDLEGAME_FULLMOVE


def compute_boundaries(uci_moves: Sequence[str], book_depth: int) -> PhaseBoundaries:
    """Compute both boundaries by replaying ``uci_moves`` from the start position."""
    board = chess.Board()
    mg_start: int | None = None
    eg_start: int | None = None
    for ply, uci in enumerate(uci_moves, start=1):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"illegal move {uci} at ply {ply}")
        board.push(move)
        if mg_start is None and ply > book_depth and _is_middlegame_position(board):
            mg_start = ply
        if (
            mg_start is not None
            and eg_start is None
            and ply >= mg_start
            and heavy_piece_count(board) <= ENDGAME_MAX_PIECES
        ):
            eg_start = ply
    return PhaseBoundaries(mg_start_ply=mg_start, eg_start_ply=eg_start)


def phase_of(ply: int, mg_start_ply: int | None, eg_start_ply: int | None) -> Phase:
    """The phase a move at ``ply`` belongs to."""
    if eg_start_ply is not None and ply >= eg_start_ply:
        return Phase.ENDGAME
    if mg_start_ply is not None and ply >= mg_start_ply:
        return Phase.MIDDLEGAME
    return Phase.OPENING
