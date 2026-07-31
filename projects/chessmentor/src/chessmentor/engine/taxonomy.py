"""FR-11 — mistake taxonomy (hard part B3).

Moves already flagged by severity (>= mistake) get exactly one category, decided
by the **first matching rule in fixed precedence order**:

1. ``allowed_mate``      2. ``missed_mate``       3. ``bad_trade``
4. ``hung_piece``        5. ``missed_tactic``     6. ``allowed_tactic``
7. ``endgame_technique`` 8. ``opening_principle`` 9. ``positional_drift``

Detectors use the analyst PV, static exchange evaluation and python-chess attack
maps.  Motif subtags (fork / pin / skewer / hanging_capture / other) annotate
missed and allowed tactics.  Every categorization stores machine-readable
evidence.

The taxonomy only ever labels moves flagged by severity — it explains losses, it
does not police style.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import chess

from ..constants import (
    BAD_TRADE_SEE_CP,
    FORK_MIN_PIECE_VALUE,
    HUNG_PIECE_SEE_CP,
    MATE_THRESHOLD,
    MISSED_TACTIC_CP,
    MISSED_TACTIC_MATERIAL_CP,
    SPOILED_DRAW_HI,
    SPOILED_DRAW_LO,
    SPOILED_DRAW_TO,
    SPOILED_WIN_FROM,
    SPOILED_WIN_TO,
)
from ..models import MistakeCategory, Motif, Phase
from .evaluate import PIECE_VALUE, material_balance
from .phase import undeveloped_minors

__all__ = [
    "Classification",
    "MoveContext",
    "classify",
    "detect_fork",
    "detect_pin_or_skewer",
    "least_valuable_attacker",
    "piece_move_counts",
    "static_exchange_evaluation",
]

_SLIDING_DIRECTIONS: dict[chess.PieceType, tuple[tuple[int, int], ...]] = {
    chess.BISHOP: ((1, 1), (1, -1), (-1, 1), (-1, -1)),
    chess.ROOK: ((1, 0), (-1, 0), (0, 1), (0, -1)),
    chess.QUEEN: ((1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)),
}


# --------------------------------------------------------------------------- #
# Static exchange evaluation
# --------------------------------------------------------------------------- #


def least_valuable_attacker(
    board: chess.Board, occupied: int, square: int, color: chess.Color
) -> tuple[int | None, chess.PieceType | None]:
    """Cheapest attacker of ``square`` for ``color`` given a working occupancy.

    The occupancy override is what makes x-rays work: removing a piece from
    ``occupied`` exposes the slider behind it on the next call.
    """
    attackers = board.attackers_mask(color, square, occupied) & occupied
    if not attackers:
        return None, None
    for piece_type in (
        chess.PAWN,
        chess.KNIGHT,
        chess.BISHOP,
        chess.ROOK,
        chess.QUEEN,
        chess.KING,
    ):
        subset = attackers & board.pieces_mask(piece_type, color)
        if subset:
            return chess.lsb(subset), piece_type
    return None, None


def static_exchange_evaluation(board: chess.Board, move: chess.Move) -> int:
    """Centipawn outcome of the capture sequence ``move`` starts, for the mover.

    The classic swap algorithm (Chess Programming Wiki) with x-rays handled by
    recomputing attackers against a shrinking occupancy.  Quiet moves get
    ``gain[0] = 0``, so the result is what the opponent wins by capturing the
    piece that just arrived.
    """
    attacker_type = board.piece_type_at(move.from_square)
    if attacker_type is None:
        raise ValueError(f"{move.uci()} does not move a piece")

    to_square = move.to_square
    occupied = board.occupied

    if board.is_en_passant(move):
        captured_value = PIECE_VALUE[chess.PAWN]
        captured_square = to_square + (-8 if board.turn == chess.WHITE else 8)
        occupied &= ~chess.BB_SQUARES[captured_square]
    else:
        victim = board.piece_type_at(to_square)
        captured_value = PIECE_VALUE[victim] if victim is not None else 0

    gain: list[int] = [0] * 34
    gain[0] = captured_value
    on_square_value = PIECE_VALUE[attacker_type]
    if move.promotion:
        gain[0] += PIECE_VALUE[move.promotion] - PIECE_VALUE[chess.PAWN]
        on_square_value = PIECE_VALUE[move.promotion]

    from_mask = chess.BB_SQUARES[move.from_square]
    side = not board.turn
    promotion_rank = chess.square_rank(to_square) in (0, 7)

    depth = 0
    while depth < len(gain) - 2:
        depth += 1
        gain[depth] = on_square_value - gain[depth - 1]
        occupied &= ~from_mask
        square, piece_type = least_valuable_attacker(board, occupied, to_square, side)
        if square is None or piece_type is None:
            break
        if piece_type is chess.KING:
            # A king may only recapture when the square is then undefended.
            remaining = occupied & ~chess.BB_SQUARES[square]
            if least_valuable_attacker(board, remaining, to_square, not side)[0] is not None:
                break
        from_mask = chess.BB_SQUARES[square]
        on_square_value = PIECE_VALUE[piece_type]
        if piece_type is chess.PAWN and promotion_rank:
            on_square_value = PIECE_VALUE[chess.QUEEN]
        side = not side

    while depth > 1:
        depth -= 1
        gain[depth - 1] = -max(-gain[depth - 1], gain[depth])
    return gain[0]


# --------------------------------------------------------------------------- #
# Motif detectors
# --------------------------------------------------------------------------- #


def detect_fork(
    board_after: chess.Board, to_square: int, mover: chess.Color
) -> dict[str, Any] | None:
    """The piece on ``to_square`` attacks >= 2 enemy pieces worth >= 300, or a piece + king."""
    piece = board_after.piece_at(to_square)
    if piece is None or piece.color != mover:
        return None
    targets: list[tuple[str, str]] = []
    king_attacked = False
    other_attacked = 0
    valuable = 0
    for square in chess.SquareSet(board_after.attacks_mask(to_square)):
        target = board_after.piece_at(square)
        if target is None or target.color == mover:
            continue
        targets.append((chess.square_name(square), chess.piece_symbol(target.piece_type)))
        if target.piece_type is chess.KING:
            king_attacked = True
            continue
        other_attacked += 1
        if PIECE_VALUE[target.piece_type] >= FORK_MIN_PIECE_VALUE:
            valuable += 1
    if valuable >= 2 or (king_attacked and other_attacked >= 1):
        return {
            "motif": "fork",
            "from_square": chess.square_name(to_square),
            "targets": sorted(targets),
        }
    return None


def _first_two_on_ray(
    board: chess.Board, square: int, delta_file: int, delta_rank: int
) -> tuple[int, int] | None:
    """The first two occupied squares along a ray from ``square``."""
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    found: list[int] = []
    while True:
        file_index += delta_file
        rank_index += delta_rank
        if not (0 <= file_index <= 7 and 0 <= rank_index <= 7):
            return None
        current = chess.square(file_index, rank_index)
        if board.piece_at(current) is not None:
            found.append(current)
            if len(found) == 2:
                return found[0], found[1]
    return None


def _alignments(board: chess.Board, square: int, mover: chess.Color) -> list[tuple[int, int]]:
    """Enemy (front, back) pairs a slider on ``square`` lines up against."""
    piece = board.piece_at(square)
    if piece is None or piece.color != mover:
        return []
    directions = _SLIDING_DIRECTIONS.get(piece.piece_type)
    if directions is None:
        return []
    pairs: list[tuple[int, int]] = []
    for delta_file, delta_rank in directions:
        pair = _first_two_on_ray(board, square, delta_file, delta_rank)
        if pair is None:
            continue
        front, back = pair
        front_piece = board.piece_at(front)
        back_piece = board.piece_at(back)
        if (
            front_piece is not None
            and back_piece is not None
            and front_piece.color != mover
            and back_piece.color != mover
        ):
            pairs.append((front, back))
    return pairs


def detect_pin_or_skewer(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    mover: chess.Color,
) -> dict[str, Any] | None:
    """A sliding piece newly aligned through two enemy pieces.

    Pin when the front piece is worth less than the back piece, skewer otherwise.
    """
    after = _alignments(board_after, move.to_square, mover)
    if not after:
        return None
    # "Newly aligned": the same pair was not already lined up from the old square.
    before = set(_alignments(board_before, move.from_square, mover))
    for front, back in after:
        if (front, back) in before:
            continue
        front_piece = board_after.piece_at(front)
        back_piece = board_after.piece_at(back)
        if front_piece is None or back_piece is None:
            continue
        front_value = PIECE_VALUE[front_piece.piece_type]
        back_value = PIECE_VALUE[back_piece.piece_type]
        motif = "pin" if front_value < back_value else "skewer"
        return {
            "motif": motif,
            "from_square": chess.square_name(move.to_square),
            "front": chess.square_name(front),
            "back": chess.square_name(back),
            "front_value": front_value,
            "back_value": back_value,
        }
    return None


def _geometry_motif(
    board_before: chess.Board, move: chess.Move, mover: chess.Color
) -> tuple[Motif, dict[str, Any]] | None:
    """Fork / pin / skewer created by ``move`` played on ``board_before``."""
    board_after = board_before.copy(stack=False)
    board_after.push(move)
    fork = detect_fork(board_after, move.to_square, mover)
    if fork is not None:
        return Motif.FORK, fork
    geometry = detect_pin_or_skewer(board_before, board_after, move, mover)
    if geometry is not None:
        return (Motif.PIN if geometry["motif"] == "pin" else Motif.SKEWER), geometry
    return None


# --------------------------------------------------------------------------- #
# Piece-move history (opening-principle rule (a))
# --------------------------------------------------------------------------- #


def piece_move_counts(uci_moves: Sequence[str]) -> tuple[dict[int, int], dict[int, int]]:
    """Track piece *instances* through a game.

    Returns ``(square_to_instance, moves_per_instance)`` after replaying
    ``uci_moves``: instances are identified by their starting square, so
    "the third move of the same piece" is answerable regardless of where it now
    stands.
    """
    board = chess.Board()
    square_to_instance = {square: square for square in board.piece_map()}
    moves_per_instance: dict[int, int] = dict.fromkeys(square_to_instance, 0)

    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        captured_square: int | None = None
        if board.is_en_passant(move):
            captured_square = move.to_square + (-8 if board.turn == chess.WHITE else 8)
        elif board.piece_at(move.to_square) is not None:
            captured_square = move.to_square
        if captured_square is not None:
            square_to_instance.pop(captured_square, None)

        if board.is_castling(move):
            rook_from, rook_to = _castling_rook_squares(board, move)
            rook_instance = square_to_instance.pop(rook_from, None)
            if rook_instance is not None:
                square_to_instance[rook_to] = rook_instance
                moves_per_instance[rook_instance] += 1

        instance = square_to_instance.pop(move.from_square, None)
        if instance is not None:
            square_to_instance[move.to_square] = instance
            moves_per_instance[instance] += 1
        board.push(move)
    return square_to_instance, moves_per_instance


def _castling_rook_squares(board: chess.Board, move: chess.Move) -> tuple[int, int]:
    king_side = chess.square_file(move.to_square) > chess.square_file(move.from_square)
    rank = chess.square_rank(move.from_square)
    if king_side:
        return chess.square(7, rank), chess.square(5, rank)
    return chess.square(0, rank), chess.square(3, rank)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MoveContext:
    """Everything the FR-11 rules need about one flagged player move."""

    board_before: chess.Board
    move: chess.Move
    player_color: chess.Color
    phase: Phase
    cp_best: int
    cp_played: int
    w_before: float
    w_after: float
    best_uci: str | None
    best_pv: Sequence[str] = field(default_factory=tuple)
    reply_pv: Sequence[str] = field(default_factory=tuple)
    prior_moves: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class Classification:
    """One category, one motif, and the machine-readable facts behind them."""

    category: MistakeCategory
    motif: Motif
    evidence: dict[str, Any]


def classify(context: MoveContext) -> Classification:
    """Apply the FR-11 rules in precedence order; the first match wins."""
    board_after = context.board_before.copy(stack=False)
    board_after.push(context.move)

    for rule in (
        _rule_allowed_mate,
        _rule_missed_mate,
        _rule_bad_trade,
        _rule_hung_piece,
        _rule_missed_tactic,
        _rule_allowed_tactic,
        _rule_endgame_technique,
        _rule_opening_principle,
    ):
        result = rule(context, board_after)
        if result is not None:
            return result
    return Classification(
        category=MistakeCategory.POSITIONAL_DRIFT,
        motif=Motif.NONE,
        evidence={
            "rule": "positional_drift",
            "cp_loss": max(0, context.cp_best - context.cp_played),
            "note": "evaluation loss with no detected tactical or material pattern",
        },
    )


def _rule_allowed_mate(context: MoveContext, board_after: chess.Board) -> Classification | None:
    if context.cp_played <= -MATE_THRESHOLD and context.cp_best > -MATE_THRESHOLD:
        return Classification(
            category=MistakeCategory.ALLOWED_MATE,
            motif=Motif.NONE,
            evidence={
                "rule": "allowed_mate",
                "cp_played": context.cp_played,
                "cp_best": context.cp_best,
                "mating_line": list(context.reply_pv[:6]),
            },
        )
    return None


def _rule_missed_mate(context: MoveContext, board_after: chess.Board) -> Classification | None:
    if context.cp_best >= MATE_THRESHOLD and context.cp_played < MATE_THRESHOLD:
        return Classification(
            category=MistakeCategory.MISSED_MATE,
            motif=Motif.NONE,
            evidence={
                "rule": "missed_mate",
                "cp_best": context.cp_best,
                "cp_played": context.cp_played,
                "best_uci": context.best_uci,
                "mating_line": list(context.best_pv[:6]),
            },
        )
    return None


def _rule_bad_trade(context: MoveContext, board_after: chess.Board) -> Classification | None:
    if not context.board_before.is_capture(context.move):
        return None
    see = static_exchange_evaluation(context.board_before, context.move)
    if see <= BAD_TRADE_SEE_CP:
        return Classification(
            category=MistakeCategory.BAD_TRADE,
            motif=Motif.NONE,
            evidence={
                "rule": "bad_trade",
                "capture_uci": context.move.uci(),
                "see_cp": see,
            },
        )
    return None


def _null_move_position(board: chess.Board) -> chess.Board:
    """The same position with the other side to move (for "could this have happened?")."""
    probe = board.copy(stack=False)
    probe.turn = not probe.turn
    probe.ep_square = None
    return probe


def _rule_hung_piece(context: MoveContext, board_after: chess.Board) -> Classification | None:
    if not context.reply_pv:
        return None
    reply = chess.Move.from_uci(context.reply_pv[0])
    if reply not in board_after.legal_moves or not board_after.is_capture(reply):
        return None
    see_after = static_exchange_evaluation(board_after, reply)
    if see_after < HUNG_PIECE_SEE_CP:
        return None

    probe = _null_move_position(context.board_before)
    if reply in probe.legal_moves:
        see_before = static_exchange_evaluation(probe, reply)
        if see_before >= HUNG_PIECE_SEE_CP:
            return None
    else:
        see_before = None

    return Classification(
        category=MistakeCategory.HUNG_PIECE,
        motif=Motif.NONE,
        evidence={
            "rule": "hung_piece",
            "capture_uci": reply.uci(),
            "see_cp": see_after,
            "pre_move_see_cp": see_before,
        },
    )


def _pv_material_delta(
    board: chess.Board, pv: Sequence[str], color: chess.Color, plies: int
) -> int:
    """Material swing for ``color`` over the first ``plies`` of ``pv``."""
    probe = board.copy(stack=False)
    before = material_balance(probe, color)
    for uci in list(pv)[:plies]:
        move = chess.Move.from_uci(uci)
        if move not in probe.legal_moves:
            break
        probe.push(move)
    return material_balance(probe, color) - before


def _rule_missed_tactic(context: MoveContext, board_after: chess.Board) -> Classification | None:
    if context.cp_best - context.cp_played < MISSED_TACTIC_CP:
        return None
    if not context.best_pv:
        return None
    delta = _pv_material_delta(context.board_before, context.best_pv, context.player_color, 4)
    if delta < MISSED_TACTIC_MATERIAL_CP:
        return None

    best_move = chess.Move.from_uci(context.best_pv[0])
    motif = Motif.OTHER
    evidence: dict[str, Any] = {
        "rule": "missed_tactic",
        "best_uci": best_move.uci(),
        "best_line": list(context.best_pv[:6]),
        "material_delta_cp": delta,
        "cp_gain": context.cp_best - context.cp_played,
    }
    geometry = _geometry_motif(context.board_before, best_move, context.player_color)
    if geometry is not None:
        motif, facts = geometry
        evidence |= facts
    elif context.board_before.is_capture(best_move):
        see = static_exchange_evaluation(context.board_before, best_move)
        if see >= HUNG_PIECE_SEE_CP:
            motif = Motif.HANGING_CAPTURE
            evidence |= {"motif": "hanging_capture", "see_cp": see}
    return Classification(category=MistakeCategory.MISSED_TACTIC, motif=motif, evidence=evidence)


def _rule_allowed_tactic(context: MoveContext, board_after: chess.Board) -> Classification | None:
    if not context.reply_pv:
        return None
    reply = chess.Move.from_uci(context.reply_pv[0])
    if reply not in board_after.legal_moves:
        return None
    geometry = _geometry_motif(board_after, reply, not context.player_color)
    if geometry is None:
        return None
    motif, facts = geometry
    return Classification(
        category=MistakeCategory.ALLOWED_TACTIC,
        motif=motif,
        evidence={
            "rule": "allowed_tactic",
            "reply_uci": reply.uci(),
            "reply_line": list(context.reply_pv[:6]),
            **facts,
        },
    )


def _rule_endgame_technique(
    context: MoveContext, board_after: chess.Board
) -> Classification | None:
    if context.phase is not Phase.ENDGAME:
        return None
    evidence: dict[str, Any] = {
        "rule": "endgame_technique",
        "w_before": context.w_before,
        "w_after": context.w_after,
    }
    if context.w_before >= SPOILED_WIN_FROM and context.w_after <= SPOILED_WIN_TO:
        evidence["note"] = "spoiled_win"
    elif (
        SPOILED_DRAW_LO <= context.w_before <= SPOILED_DRAW_HI
        and context.w_after <= SPOILED_DRAW_TO
    ):
        evidence["note"] = "spoiled_draw"
    return Classification(
        category=MistakeCategory.ENDGAME_TECHNIQUE, motif=Motif.NONE, evidence=evidence
    )


def _rule_opening_principle(
    context: MoveContext, board_after: chess.Board
) -> Classification | None:
    if context.phase is not Phase.OPENING:
        return None
    color = context.player_color
    undeveloped_before = undeveloped_minors(context.board_before, color)

    # (a) third-or-later move of the same piece with >= 2 own minors at home.
    square_to_instance, moves_per_instance = piece_move_counts(context.prior_moves)
    instance = square_to_instance.get(context.move.from_square)
    repeats = moves_per_instance.get(instance, 0) if instance is not None else 0
    if repeats >= 2 and undeveloped_before >= 2:
        return Classification(
            category=MistakeCategory.OPENING_PRINCIPLE,
            motif=Motif.NONE,
            evidence={
                "rule": "opening_principle",
                "violation": "repeated_piece_move",
                "prior_moves_of_piece": repeats,
                "undeveloped_minors": undeveloped_before,
            },
        )

    # (b) queen beyond its third rank with >= 3 own minors at home.
    for square in board_after.pieces(chess.QUEEN, color):
        relative_rank = (
            chess.square_rank(square) if color == chess.WHITE else 7 - chess.square_rank(square)
        )
        if relative_rank >= 3 and undeveloped_before >= 3:
            return Classification(
                category=MistakeCategory.OPENING_PRINCIPLE,
                motif=Motif.NONE,
                evidence={
                    "rule": "opening_principle",
                    "violation": "early_queen",
                    "queen_square": chess.square_name(square),
                    "undeveloped_minors": undeveloped_before,
                },
            )

    # (c) king uncastled after fullmove 10 with castling rights intact.
    if board_after.fullmove_number > 10 and board_after.has_castling_rights(color):
        king_square = board_after.king(color)
        home = chess.E1 if color == chess.WHITE else chess.E8
        if king_square == home:
            return Classification(
                category=MistakeCategory.OPENING_PRINCIPLE,
                motif=Motif.NONE,
                evidence={
                    "rule": "opening_principle",
                    "violation": "uncastled_king",
                    "fullmove": board_after.fullmove_number,
                },
            )
    return None
