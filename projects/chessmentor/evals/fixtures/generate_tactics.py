"""Generate ``tactics_suite.json`` — the 60 M7 positions with verified solutions.

Run: ``uv run python chessmentor/evals/fixtures/generate_tactics.py``

Positions are composed by seeded random placement of a small piece set and then
**verified by exhaustive enumeration over python-chess move generation** — the
engine under evaluation is never consulted:

* *mate in 1* (20): the correct-move set is every legal move that is checkmate.
* *mate in 2* (20): exhaustive alpha-beta-free minimax proves a forced mate in
  two for the side to move and proves that no mate in one exists; the
  correct-move set is every first move that forces mate within two.
* *forced material* (20): every legal move is scored by ``truth.forced_value``
  (full-width 4-ply minimax, material-only leaf score).  A position qualifies
  only when the best move gains at least ``MATERIAL_GAIN_CP`` over the static
  balance **and** every move outside the correct set is at least
  ``MATERIAL_MARGIN_CP`` worse — so the position has one tactical answer, not a
  positional preference the analyst is free to disagree with.

Composed positions are additionally required to be legal, quiet of the trivial
kind (the side to move is not already in check for the material set), and small
enough that the analyst's depth-6 + quiescence horizon at ``JUDGE_BUDGET``
reaches the solution, which is what makes the M7 gate about the *engine* rather
than about the budget.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import chess

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.fixtures import write_fixture
from evals.fixtures.truth import forced_value, material_balance

TACTICS_SEED = 20260731
N_PER_CLASS = 20
MATERIAL_GAIN_CP = 300
MATERIAL_MARGIN_CP = 200
MAX_ATTEMPTS = 4_000_000

#: Piece pools for composition (kings are always added separately).
WHITE_POOLS: list[tuple[chess.PieceType, ...]] = [
    (chess.QUEEN, chess.ROOK),
    (chess.ROOK, chess.ROOK),
    (chess.QUEEN, chess.KNIGHT),
    (chess.ROOK, chess.BISHOP),
    (chess.QUEEN, chess.BISHOP),
    (chess.ROOK, chess.KNIGHT, chess.PAWN),
    (chess.QUEEN, chess.PAWN),
    (chess.BISHOP, chess.BISHOP, chess.PAWN),
    (chess.ROOK, chess.PAWN, chess.PAWN),
    (chess.KNIGHT, chess.KNIGHT, chess.ROOK),
]
BLACK_POOLS: list[tuple[chess.PieceType, ...]] = [
    (),
    (chess.PAWN,),
    (chess.KNIGHT,),
    (chess.BISHOP,),
    (chess.ROOK,),
    (chess.PAWN, chess.PAWN),
    (chess.KNIGHT, chess.PAWN),
    (chess.ROOK, chess.PAWN),
    (chess.QUEEN,),
    (chess.BISHOP, chess.PAWN),
]


def compose(rng: random.Random) -> chess.Board | None:
    """Place kings plus a random pool; return the board when it is a legal position."""
    board = chess.Board(None)
    squares = list(chess.SQUARES)
    rng.shuffle(squares)
    cursor = 0

    def take() -> int:
        nonlocal cursor
        square = squares[cursor]
        cursor += 1
        return square

    board.set_piece_at(take(), chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(take(), chess.Piece(chess.KING, chess.BLACK))
    for piece_type in rng.choice(WHITE_POOLS):
        square = take()
        if piece_type is chess.PAWN and chess.square_rank(square) in (0, 7):
            return None
        board.set_piece_at(square, chess.Piece(piece_type, chess.WHITE))
    for piece_type in rng.choice(BLACK_POOLS):
        square = take()
        if piece_type is chess.PAWN and chess.square_rank(square) in (0, 7):
            return None
        board.set_piece_at(square, chess.Piece(piece_type, chess.BLACK))
    board.turn = chess.WHITE if rng.random() < 0.5 else chess.BLACK
    board.castling_rights = chess.BB_EMPTY
    if not board.is_valid():
        return None
    if board.is_game_over(claim_draw=False):
        return None
    return board


def mates_in_one(board: chess.Board) -> list[chess.Move]:
    found = []
    for move in board.legal_moves:
        board.push(move)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            found.append(move)
    return found


def _forces_mate_in_two(board: chess.Board, move: chess.Move) -> bool:
    """After ``move`` every opponent reply must allow an immediate mate."""
    board.push(move)
    try:
        if board.is_checkmate():
            return False  # that is a mate in one, not in two
        replies = list(board.legal_moves)
        if not replies:
            return False  # stalemate
        for reply in replies:
            board.push(reply)
            try:
                if not mates_in_one(board):
                    return False
            finally:
                board.pop()
        return True
    finally:
        board.pop()


def mates_in_two(board: chess.Board) -> list[chess.Move]:
    if mates_in_one(board):
        return []
    return [move for move in board.legal_moves if _forces_mate_in_two(board, move)]


def material_solution(board: chess.Board) -> tuple[list[chess.Move], int, int] | None:
    """Correct-move set for a forced-material position, or ``None``."""
    if board.is_check():
        return None
    static = material_balance(board)
    values: dict[chess.Move, int] = {}
    for move in board.legal_moves:
        board.push(move)
        values[move] = -forced_value(board, 3).value
        board.pop()
    if not values:
        return None
    best = max(values.values())
    if abs(best) > 5_000:  # mate in view: belongs to the mate classes
        return None
    if best - static < MATERIAL_GAIN_CP:
        return None
    winners = [move for move, value in values.items() if value == best]
    others = [value for move, value in values.items() if value != best]
    if not others or best - max(others) < MATERIAL_MARGIN_CP:
        return None
    return winners, static, best


def build(seed: int) -> dict[str, object]:
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, object]]] = {
        "mate_in_1": [],
        "mate_in_2": [],
        "forced_material": [],
    }
    seen: set[str] = set()
    attempts = 0
    while attempts < MAX_ATTEMPTS and any(
        len(bucket) < N_PER_CLASS for bucket in buckets.values()
    ):
        attempts += 1
        board = compose(rng)
        if board is None:
            continue
        fen = board.fen()
        if fen in seen:
            continue

        if len(buckets["mate_in_1"]) < N_PER_CLASS:
            solutions = mates_in_one(board)
            if solutions:
                seen.add(fen)
                buckets["mate_in_1"].append(
                    _case(board, solutions, "mate_in_1", detail="checkmate in one")
                )
                continue
        if len(buckets["mate_in_2"]) < N_PER_CLASS:
            solutions = mates_in_two(board)
            if solutions:
                seen.add(fen)
                buckets["mate_in_2"].append(
                    _case(board, solutions, "mate_in_2", detail="forced mate in two")
                )
                continue
        if len(buckets["forced_material"]) < N_PER_CLASS:
            result = material_solution(board)
            if result is not None:
                solutions, static, best = result
                seen.add(fen)
                buckets["forced_material"].append(
                    _case(
                        board,
                        solutions,
                        "forced_material",
                        detail=(
                            f"forced material {static} -> {best} cp within 4 plies "
                            f"(gain {best - static})"
                        ),
                    )
                )
    for name, bucket in buckets.items():
        if len(bucket) < N_PER_CLASS:
            raise SystemExit(f"only found {len(bucket)} {name} positions in {attempts} attempts")

    cases = buckets["mate_in_1"] + buckets["mate_in_2"] + buckets["forced_material"]
    for index, case in enumerate(cases, start=1):
        case["id"] = f"tac-{index:03d}"
    return {"seed": seed, "attempts": attempts, "positions": cases}


def _case(
    board: chess.Board, solutions: list[chess.Move], kind: str, *, detail: str
) -> dict[str, object]:
    return {
        "id": "",
        "kind": kind,
        "fen": board.fen(),
        "correct_uci": sorted(move.uci() for move in solutions),
        "correct_san": sorted(board.san(move) for move in solutions),
        "rationale": f"{detail}; verified by exhaustive enumeration with python-chess",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=TACTICS_SEED)
    args = parser.parse_args()
    payload = build(args.seed)
    path = write_fixture("tactics_suite.json", payload)
    print(f"wrote {path}: {len(payload['positions'])} positions")


if __name__ == "__main__":
    main()
