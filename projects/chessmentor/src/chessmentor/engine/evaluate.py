"""FR-3 — tapered evaluation.

Material + piece-square tables using the published PeSTO midgame/endgame values
(Chess Programming Wiki; lineage: Michniewski's Simplified Evaluation Function),
Fruit-style phase interpolation over a 0-24 game phase (minor = 1, rook = 2,
queen = 4), plus a passed-pawn bonus by rank and a 10 cp tempo term.

Output is in centipawns **from the side to move** (negamax convention) and is a
pure function of the position.
"""

from __future__ import annotations

import chess

__all__ = [
    "GAME_PHASE_MAX",
    "MG_PIECE_VALUE",
    "EG_PIECE_VALUE",
    "PIECE_VALUE",
    "TEMPO_CP",
    "evaluate",
    "game_phase",
    "material_balance",
]

TEMPO_CP = 10
GAME_PHASE_MAX = 24

#: Phase weights (Fruit-style): minor 1, rook 2, queen 4 → 24 at the start.
_PHASE_WEIGHT: dict[chess.PieceType, int] = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
    chess.KING: 0,
}

#: PeSTO material values (midgame / endgame), centipawns.
MG_PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN: 82,
    chess.KNIGHT: 337,
    chess.BISHOP: 365,
    chess.ROOK: 477,
    chess.QUEEN: 1025,
    chess.KING: 0,
}
EG_PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN: 94,
    chess.KNIGHT: 281,
    chess.BISHOP: 297,
    chess.ROOK: 512,
    chess.QUEEN: 936,
    chess.KING: 0,
}

#: Plain material values (Shannon lineage) used by SEE and by material deltas.
PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20_000,
}

#: Passed-pawn bonus indexed by the pawn's rank *relative to its own side* (0-7).
PASSED_PAWN_BONUS: tuple[int, ...] = (0, 5, 10, 20, 35, 60, 100, 0)

# --------------------------------------------------------------------------- #
# PeSTO piece-square tables.
#
# Each table is written as printed on the Chess Programming Wiki: index 0 is a8
# and index 63 is h1.  For a python-chess square ``sq`` (a1 = 0, h8 = 63) the
# table index is ``sq ^ 56`` for a White piece and ``sq`` for a Black piece.
# --------------------------------------------------------------------------- #

_MG_PAWN = (
      0,   0,   0,   0,   0,   0,   0,   0,
     98, 134,  61,  95,  68, 126,  34, -11,
     -6,   7,  26,  31,  65,  56,  25, -20,
    -14,  13,   6,  21,  23,  12,  17, -23,
    -27,  -2,  -5,  12,  17,   6,  10, -25,
    -26,  -4,  -4, -10,   3,   3,  33, -12,
    -35,  -1, -20, -23, -15,  24,  38, -22,
      0,   0,   0,   0,   0,   0,   0,   0,
)  # fmt: skip

_EG_PAWN = (
      0,   0,   0,   0,   0,   0,   0,   0,
    178, 173, 158, 134, 147, 132, 165, 187,
     94, 100,  85,  67,  56,  53,  82,  84,
     32,  24,  13,   5,  -2,   4,  17,  17,
     13,   9,  -3,  -7,  -7,  -8,   3,  -1,
      4,   7,  -6,   1,   0,  -5,  -1,  -8,
     13,   8,   8,  10,  13,   0,   2,  -7,
      0,   0,   0,   0,   0,   0,   0,   0,
)  # fmt: skip

_MG_KNIGHT = (
   -167, -89, -34, -49,  61, -97, -15, -107,
    -73, -41,  72,  36,  23,  62,   7,  -17,
    -47,  60,  37,  65,  84, 129,  73,   44,
     -9,  17,  19,  53,  37,  69,  18,   22,
    -13,   4,  16,  13,  28,  19,  21,   -8,
    -23,  -9,  12,  10,  19,  17,  25,  -16,
    -29, -53, -12,  -3,  -1,  18, -14,  -19,
   -105, -21, -58, -33, -17, -28, -19,  -23,
)  # fmt: skip

_EG_KNIGHT = (
    -58, -38, -13, -28, -31, -27, -63, -99,
    -25,  -8, -25,  -2,  -9, -25, -24, -52,
    -24, -20,  10,   9,  -1,  -9, -19, -41,
    -17,   3,  22,  22,  22,  11,   8, -18,
    -18,  -6,  16,  25,  16,  17,   4, -18,
    -23,  -3,  -1,  15,  10,  -3, -20, -22,
    -42, -20, -10,  -5,  -2, -20, -23, -44,
    -29, -51, -23, -15, -22, -18, -50, -64,
)  # fmt: skip

_MG_BISHOP = (
    -29,   4, -82, -37, -25, -42,   7,  -8,
    -26,  16, -18, -13,  30,  59,  18, -47,
    -16,  37,  43,  40,  35,  50,  37,  -2,
     -4,   5,  19,  50,  37,  37,   7,  -2,
     -6,  13,  13,  26,  34,  12,  10,   4,
      0,  15,  15,  15,  14,  27,  18,  10,
      4,  15,  16,   0,   7,  21,  33,   1,
    -33,  -3, -14, -21, -13, -12, -39, -21,
)  # fmt: skip

_EG_BISHOP = (
    -14, -21, -11,  -8,  -7,  -9, -17, -24,
     -8,  -4,   7, -12,  -3, -13,  -4, -14,
      2,  -8,   0,  -1,  -2,   6,   0,   4,
     -3,   9,  12,   9,  14,  10,   3,   2,
     -6,   3,  13,  19,   7,  10,  -3,  -9,
    -12,  -3,   8,  10,  13,   3,  -7, -15,
    -14, -18,  -7,  -1,   4,  -9, -15, -27,
    -23,  -9, -23,  -5,  -9, -16,  -5, -17,
)  # fmt: skip

_MG_ROOK = (
     32,  42,  32,  51,  63,   9,  31,  43,
     27,  32,  58,  62,  80,  67,  26,  44,
     -5,  19,  26,  36,  17,  45,  61,  16,
    -24, -11,   7,  26,  24,  35,  -8, -20,
    -36, -26, -12,  -1,   9,  -7,   6, -23,
    -45, -25, -16, -17,   3,   0,  -5, -33,
    -44, -16, -20,  -9,  -1,  11,  -6, -71,
    -19, -13,   1,  17,  16,   7, -37, -26,
)  # fmt: skip

_EG_ROOK = (
     13,  10,  18,  15,  12,  12,   8,   5,
     11,  13,  13,  11,  -3,   3,   8,   3,
      7,   7,   7,   5,   4,  -3,  -5,  -3,
      4,   3,  13,   1,   2,   1,  -1,   2,
      3,   5,   8,   4,  -5,  -6,  -8, -11,
     -4,   0,  -5,  -1,  -7, -12,  -8, -16,
     -6,  -6,   0,   2,  -9,  -9, -11,  -3,
     -9,   2,   3,  -1,  -5, -13,   4, -20,
)  # fmt: skip

_MG_QUEEN = (
    -28,   0,  29,  12,  59,  44,  43,  45,
    -24, -39,  -5,   1, -16,  57,  28,  54,
    -13, -17,   7,   8,  29,  56,  47,  57,
    -27, -27, -16, -16,  -1,  17,  -2,   1,
     -9, -26,  -9, -10,  -2,  -4,   3,  -3,
    -14,   2, -11,  -2,  -5,   2,  14,   5,
    -35,  -8,  11,   2,   8,  15,  -3,   1,
     -1, -18,  -9,  10, -15, -25, -31, -50,
)  # fmt: skip

_EG_QUEEN = (
     -9,  22,  22,  27,  27,  19,  10,  20,
    -17,  20,  32,  41,  58,  25,  30,   0,
    -20,   6,   9,  49,  47,  35,  19,   9,
      3,  22,  24,  45,  57,  40,  57,  36,
    -18,  28,  19,  47,  31,  34,  39,  23,
    -16, -27,  15,   6,   9,  17,  10,   5,
    -22, -23, -30, -16, -16, -23, -36, -32,
    -33, -28, -22, -43,  -5, -32, -20, -41,
)  # fmt: skip

_MG_KING = (
    -65,  23,  16, -15, -56, -34,   2,  13,
     29,  -1, -20,  -7,  -8,  -4, -38, -29,
     -9,  24,   2, -16, -20,   6,  22, -22,
    -17, -20, -12, -27, -30, -25, -14, -36,
    -49,  -1, -27, -39, -46, -44, -33, -51,
    -14, -14, -22, -46, -44, -30, -15, -27,
      1,   7,  -8, -64, -43, -16,   9,   8,
    -15,  36,  12, -54,   8, -28,  24,  14,
)  # fmt: skip

_EG_KING = (
    -74, -35, -18, -18, -11,  15,   4, -17,
    -12,  17,  14,  17,  17,  38,  23,  11,
     10,  17,  23,  15,  20,  45,  44,  13,
     -8,  22,  24,  27,  26,  33,  26,   3,
    -18,  -4,  21,  24,  27,  23,   9, -11,
    -19,  -3,  11,  21,  23,  16,   7,  -9,
    -27, -11,   4,  13,  14,   4,  -5, -17,
    -53, -34, -21, -11, -28, -14, -24, -43,
)  # fmt: skip

_MG_PST: dict[chess.PieceType, tuple[int, ...]] = {
    chess.PAWN: _MG_PAWN,
    chess.KNIGHT: _MG_KNIGHT,
    chess.BISHOP: _MG_BISHOP,
    chess.ROOK: _MG_ROOK,
    chess.QUEEN: _MG_QUEEN,
    chess.KING: _MG_KING,
}
_EG_PST: dict[chess.PieceType, tuple[int, ...]] = {
    chess.PAWN: _EG_PAWN,
    chess.KNIGHT: _EG_KNIGHT,
    chess.BISHOP: _EG_BISHOP,
    chess.ROOK: _EG_ROOK,
    chess.QUEEN: _EG_QUEEN,
    chess.KING: _EG_KING,
}


def _build_tables() -> tuple[dict[tuple[bool, int], list[int]], dict[tuple[bool, int], list[int]]]:
    """Pre-add material value to each PST entry, per colour (PeSTO ``init_tables``)."""
    mg: dict[tuple[bool, int], list[int]] = {}
    eg: dict[tuple[bool, int], list[int]] = {}
    for piece_type in chess.PIECE_TYPES:
        mg_table = _MG_PST[piece_type]
        eg_table = _EG_PST[piece_type]
        mg[(chess.WHITE, piece_type)] = [
            MG_PIECE_VALUE[piece_type] + mg_table[sq ^ 56] for sq in range(64)
        ]
        eg[(chess.WHITE, piece_type)] = [
            EG_PIECE_VALUE[piece_type] + eg_table[sq ^ 56] for sq in range(64)
        ]
        mg[(chess.BLACK, piece_type)] = [
            MG_PIECE_VALUE[piece_type] + mg_table[sq] for sq in range(64)
        ]
        eg[(chess.BLACK, piece_type)] = [
            EG_PIECE_VALUE[piece_type] + eg_table[sq] for sq in range(64)
        ]
    return mg, eg


_MG_TABLE, _EG_TABLE = _build_tables()

#: Squares in front of a pawn on its own file plus the two adjacent files.
_PASSED_MASK: dict[tuple[bool, int], int] = {}
for _color in (chess.WHITE, chess.BLACK):
    for _sq in range(64):
        _file = chess.square_file(_sq)
        _rank = chess.square_rank(_sq)
        _mask = 0
        for _f in range(max(0, _file - 1), min(7, _file + 1) + 1):
            _ranks = range(_rank + 1, 8) if _color == chess.WHITE else range(0, _rank)
            for _r in _ranks:
                _mask |= chess.BB_SQUARES[chess.square(_f, _r)]
        _PASSED_MASK[(_color, _sq)] = _mask


def game_phase(board: chess.Board) -> int:
    """Fruit-style game phase, 24 (opening material) down to 0 (bare kings)."""
    phase = 0
    for piece_type, weight in _PHASE_WEIGHT.items():
        if weight:
            phase += weight * len(board.pieces(piece_type, chess.WHITE))
            phase += weight * len(board.pieces(piece_type, chess.BLACK))
    return min(phase, GAME_PHASE_MAX)


def material_balance(board: chess.Board, color: chess.Color) -> int:
    """Plain material balance in centipawns from ``color``'s point of view."""
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        if piece_type is chess.KING:
            continue
        total += value * len(board.pieces(piece_type, color))
        total -= value * len(board.pieces(piece_type, not color))
    return total


def _passed_pawn_score(board: chess.Board, color: chess.Color) -> int:
    enemy_pawns = board.pieces_mask(chess.PAWN, not color)
    score = 0
    for sq in board.pieces(chess.PAWN, color):
        if _PASSED_MASK[(color, sq)] & enemy_pawns:
            continue
        rank = chess.square_rank(sq) if color == chess.WHITE else 7 - chess.square_rank(sq)
        score += PASSED_PAWN_BONUS[rank]
    return score


def evaluate(board: chess.Board) -> int:
    """Static evaluation in centipawns from the side to move (negamax convention)."""
    mg = 0
    eg = 0
    phase = 0
    for sq, piece in board.piece_map().items():
        key = (piece.color, piece.piece_type)
        sign = 1 if piece.color == chess.WHITE else -1
        mg += sign * _MG_TABLE[key][sq]
        eg += sign * _EG_TABLE[key][sq]
        phase += _PHASE_WEIGHT[piece.piece_type]

    phase = min(phase, GAME_PHASE_MAX)
    passed = _passed_pawn_score(board, chess.WHITE) - _passed_pawn_score(board, chess.BLACK)
    # Passed pawns matter most in the endgame; weight them by the endgame share.
    eg += passed
    mg += passed // 2

    score = (mg * phase + eg * (GAME_PHASE_MAX - phase)) // GAME_PHASE_MAX
    if board.turn == chess.BLACK:
        score = -score
    return score + TEMPO_CP
