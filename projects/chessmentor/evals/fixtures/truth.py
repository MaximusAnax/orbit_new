"""Independent ground-truth machinery for the constructed fixtures.

Nothing in this module imports ``chessmentor.engine`` — EVALS.md's first ground
rule is that ground truth never comes from the engine under evaluation.  What
lives here is:

* ``material_balance``  — Shannon-lineage material, side-to-move relative;
* ``forced_value``      — a full-width negamax over *python-chess* move
  generation with a material-only leaf score, i.e. the forced material delta
  within N plies, plus exact mate detection.  Alpha-beta only prunes provably
  irrelevant branches, so the returned value is the exact minimax value;
* ``see``               — a static exchange evaluation written from the swap
  algorithm, used to assert that a composed base position is quiet;
* ``has_contact``       — true when either side can capture anything at all
  (the strongest possible form of "quiet");
* ``win_probability`` / ``severity_of`` — the published Lichess win model and
  the ``SEV_*`` thresholds, re-implemented here from the numbers in SCOPE.md's
  constants table so the fixture truth never depends on ``engine/judge.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import chess

__all__ = [
    "CP_LOSS_CAP",
    "MATE_VALUE",
    "PIECE_VALUE",
    "SEV_BLUNDER",
    "SEV_INACCURACY",
    "SEV_MISTAKE",
    "WIN_K",
    "ForcedResult",
    "delta_w",
    "forced_value",
    "has_contact",
    "quiet_by_see",
    "material_balance",
    "see",
    "severity_of",
    "survives_perturbation",
    "win_probability",
]

#: Shannon-lineage values.  Deliberately spelled out rather than imported.
PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20_000,
}

MATE_VALUE = 32_000
WIN_K = 0.00368208
SEV_INACCURACY = 0.05
SEV_MISTAKE = 0.10
SEV_BLUNDER = 0.15
CP_LOSS_CAP = 1_000


def material_balance(board: chess.Board) -> int:
    """Material from the side to move's point of view."""
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        if piece_type is chess.KING:
            continue
        total += value * len(board.pieces(piece_type, chess.WHITE))
        total -= value * len(board.pieces(piece_type, chess.BLACK))
    return total if board.turn == chess.WHITE else -total


@dataclass(frozen=True)
class ForcedResult:
    """The exact N-ply minimax value under material-only scoring."""

    value: int
    nodes: int


def _negamax(board: chess.Board, depth: int, alpha: int, beta: int, ply: int, counter: list[int]) -> int:
    counter[0] += 1
    if board.is_checkmate():
        return -(MATE_VALUE - ply)
    if board.is_stalemate() or board.is_insufficient_material():
        return 0
    if depth == 0:
        return material_balance(board)
    best = -MATE_VALUE
    # Captures first: with a material leaf score this makes alpha-beta cut
    # almost immediately on quiet positions, without changing the value.
    moves = sorted(
        board.legal_moves,
        key=lambda m: -(PIECE_VALUE.get(t, 0) if (t := board.piece_type_at(m.to_square)) else 0),
    )
    for move in moves:
        board.push(move)
        score = -_negamax(board, depth - 1, -beta, -alpha, ply + 1, counter)
        board.pop()
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def forced_value(board: chess.Board, depth: int = 4) -> ForcedResult:
    """Exact material-only minimax value of ``board`` to ``depth`` plies."""
    counter = [0]
    value = _negamax(board.copy(stack=False), depth, -MATE_VALUE, MATE_VALUE, 0, counter)
    return ForcedResult(value=value, nodes=counter[0])


def _least_valuable_attacker(
    board: chess.Board, square: int, colour: chess.Color, occupied: chess.SquareSet
) -> tuple[int, chess.PieceType] | None:
    best: tuple[int, chess.PieceType] | None = None
    for attacker in board.attackers(colour, square):
        if attacker not in occupied:
            continue
        piece = board.piece_type_at(attacker)
        if piece is None:
            continue
        if best is None or PIECE_VALUE[piece] < PIECE_VALUE[best[1]]:
            best = (attacker, piece)
    return best


def see(board: chess.Board, move: chess.Move) -> int:
    """Static exchange evaluation of ``move`` (swap algorithm, x-rays ignored).

    Only used to certify that a composed base position is quiet, where the
    simple form is sufficient — the runtime classifier has its own full SEE.
    """
    target = board.piece_type_at(move.to_square)
    if target is None:
        return 0
    gains = [PIECE_VALUE[target]]
    occupied = chess.SquareSet(board.occupied)
    occupied.discard(move.from_square)
    moving = board.piece_type_at(move.from_square)
    assert moving is not None
    side = not board.turn
    on_square = moving
    depth = 0
    while True:
        attacker = _least_valuable_attacker(board, move.to_square, side, occupied)
        if attacker is None:
            break
        depth += 1
        gains.append(PIECE_VALUE[on_square] - gains[depth - 1])
        occupied.discard(attacker[0])
        on_square = attacker[1]
        side = not side
    for index in range(len(gains) - 1, 0, -1):
        gains[index - 1] = -max(-gains[index - 1], gains[index])
    return gains[0]


def has_contact(board: chess.Board) -> bool:
    """True when *either* side has any capture available at all."""
    for colour in (chess.WHITE, chess.BLACK):
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece is None or piece.color != colour:
                continue
            if board.attacks(square) & board.occupied_co[not colour]:
                return True
    return False


def quiet_by_see(board: chess.Board) -> bool:
    """EVALS.md's quietness test: no capture with ``|SEE| > 0`` for *either* side.

    Captures that are exactly even (``SEE == 0``) are allowed — two rooks facing
    each other down an open file do not make a position tactical.
    """
    for colour in (chess.WHITE, chess.BLACK):
        probe = board.copy(stack=False)
        probe.turn = colour
        if probe.is_check():
            return False
        for move in probe.generate_legal_captures():
            if see(probe, move) != 0:
                return False
    return True


def win_probability(cp: int) -> float:
    """``w(cp) = 1/(1+e^(-WIN_K*cp))`` on [0, 1] (SCOPE FR-9 / D8)."""
    if cp >= MATE_VALUE - 1_000:
        return 1.0
    if cp <= -(MATE_VALUE - 1_000):
        return 0.0
    return 1.0 / (1.0 + math.exp(-WIN_K * max(-20_000, min(20_000, cp))))


def delta_w(cp_best: int, cp_played: int) -> float:
    return max(0.0, win_probability(cp_best) - win_probability(cp_played))


def severity_of(value: float) -> str:
    if value >= SEV_BLUNDER:
        return "blunder"
    if value >= SEV_MISTAKE:
        return "mistake"
    if value >= SEV_INACCURACY:
        return "inaccuracy"
    return "ok"


def survives_perturbation(
    cp_best: int, cp_played: int, *, common_mode: int = 40, differential: int = 20
) -> bool:
    """EVALS.md's judgment-fixture robustness envelope.

    A common-mode shift moves both evaluations together (a PST bias in the base
    position); a differential error mis-measures the *loss*.  The truth tier
    must survive both, jointly, in all four worst-case sign combinations.
    """
    truth = severity_of(delta_w(cp_best, cp_played))
    for common in (-common_mode, common_mode):
        for diff in (-differential, differential):
            perturbed_best = cp_best + common
            perturbed_played = cp_played + common - diff
            if severity_of(delta_w(perturbed_best, perturbed_played)) != truth:
                return False
    return True


def perturbation_margin(cp_best: int, cp_played: int) -> int:
    """The largest symmetric differential error the truth tier survives (cp).

    Used to *rank* candidate cases: EVALS.md requires 20; picking the most
    robust realisation of each band centre is free headroom for M4.
    """
    for differential in range(0, 201, 5):
        if not survives_perturbation(cp_best, cp_played, common_mode=40, differential=differential):
            return max(0, differential - 5)
    return 200
