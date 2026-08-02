"""FR-6 — pure game-flow orchestration.

No I/O, no clock reads: timestamps and seeds are inputs.  The service layer
(API/CLI) persists what these functions return; the rules live here.

Terminal states are detected through python-chess only (FR-1): checkmate,
stalemate, insufficient material, resignation, plus claimable draws (threefold
repetition, fifty-move) which **end the game automatically as draws** (D15).
"""

from __future__ import annotations

import io
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import chess
import chess.pgn

from ..constants import MIN_RATED_PLIES
from ..models import (
    DECISIVE_STATUSES,
    Color,
    CpuMeta,
    GameSource,
    GameStatus,
    Level,
    MoveRecord,
    PreferredColor,
    Termination,
)
from .throttle import CpuChoice, choose_cpu_move

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the engine import-pure
    from ..adapters.book import OpeningBook

__all__ = [
    "IllegalMoveError",
    "TerminalOutcome",
    "abort_outcome",
    "apply_cpu_move",
    "apply_player_move",
    "board_from_moves",
    "derive_rated",
    "detect_terminal",
    "make_move_record",
    "parse_move",
    "resignation_outcome",
    "resolve_color",
    "to_pgn",
]


class IllegalMoveError(ValueError):
    """The submitted move is not legal in the current position."""

    def __init__(self, text: str, legal_san: Sequence[str]) -> None:
        super().__init__(f"illegal move {text!r}")
        self.text = text
        self.legal_san = list(legal_san)


@dataclass(frozen=True)
class TerminalOutcome:
    """How a game ended."""

    status: GameStatus
    termination: Termination
    result_score: float | None


_TERMINATION_MAP: dict[chess.Termination, Termination] = {
    chess.Termination.CHECKMATE: Termination.CHECKMATE,
    chess.Termination.STALEMATE: Termination.STALEMATE,
    chess.Termination.INSUFFICIENT_MATERIAL: Termination.INSUFFICIENT_MATERIAL,
    chess.Termination.THREEFOLD_REPETITION: Termination.THREEFOLD_REPETITION,
    chess.Termination.FIVEFOLD_REPETITION: Termination.THREEFOLD_REPETITION,
    chess.Termination.FIFTY_MOVES: Termination.FIFTY_MOVE_RULE,
    chess.Termination.SEVENTYFIVE_MOVES: Termination.FIFTY_MOVE_RULE,
}


def board_from_moves(uci_moves: Sequence[str]) -> chess.Board:
    """Replay UCI moves from the start position, validating each."""
    board = chess.Board()
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise IllegalMoveError(uci, sorted(board.san(m) for m in board.legal_moves))
        board.push(move)
    return board


def parse_move(board: chess.Board, text: str) -> chess.Move:
    """Accept SAN or UCI; raise :class:`IllegalMoveError` with the legal-move list."""
    cleaned = text.strip()
    move: chess.Move | None = None
    try:
        move = board.parse_san(cleaned)
    except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        try:
            candidate = chess.Move.from_uci(cleaned.lower())
        except ValueError:
            candidate = None
        if candidate is not None and candidate in board.legal_moves:
            move = candidate
    if move is None or move not in board.legal_moves:
        raise IllegalMoveError(text, sorted(board.san(m) for m in board.legal_moves))
    return move


def detect_terminal(board: chess.Board, player_color: Color) -> TerminalOutcome | None:
    """Terminal state of ``board`` from the player's point of view, or ``None``."""
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return None
    termination = _TERMINATION_MAP[outcome.termination]
    if outcome.winner is None:
        return TerminalOutcome(GameStatus.DRAW, termination, 0.5)
    player_won = outcome.winner == (player_color is Color.WHITE)
    if player_won:
        return TerminalOutcome(GameStatus.PLAYER_WIN, termination, 1.0)
    return TerminalOutcome(GameStatus.OPPONENT_WIN, termination, 0.0)


def resignation_outcome(resigning: Color, player_color: Color) -> TerminalOutcome:
    """FR-6 resignation: whoever resigns loses."""
    if resigning is player_color:
        return TerminalOutcome(GameStatus.OPPONENT_WIN, Termination.RESIGNATION, 0.0)
    return TerminalOutcome(GameStatus.PLAYER_WIN, Termination.RESIGNATION, 1.0)


def abort_outcome(ply_count: int) -> TerminalOutcome:
    """FR-6 abort: only before ply 8, never rated."""
    if ply_count >= MIN_RATED_PLIES:
        raise ValueError(f"a game may only be aborted before ply {MIN_RATED_PLIES}")
    return TerminalOutcome(GameStatus.ABORTED, Termination.ABORTED, None)


def make_move_record(
    board_before: chess.Board,
    move: chess.Move,
    ply: int,
    *,
    is_book: bool = False,
    cpu_meta: CpuMeta | None = None,
    game_id: int | None = None,
) -> MoveRecord:
    """Build the append-only record for one ply (SAN and UCI both stored)."""
    san = board_before.san(move)
    probe = board_before.copy(stack=False)
    probe.push(move)
    return MoveRecord(
        game_id=game_id,
        ply=ply,
        color=Color.WHITE if board_before.turn == chess.WHITE else Color.BLACK,
        san=san,
        uci=move.uci(),
        fen_after=probe.fen(),
        is_book=is_book,
        cpu_meta=cpu_meta,
    )


@dataclass(frozen=True)
class PlyResult:
    """One applied ply plus the terminal state it may have produced."""

    record: MoveRecord
    outcome: TerminalOutcome | None
    choice: CpuChoice | None = None


def apply_player_move(
    board: chess.Board,
    text: str,
    *,
    player_color: Color,
    game_id: int | None = None,
) -> PlyResult:
    """Validate, apply and record the player's move; ``board`` is mutated."""
    move = parse_move(board, text)
    ply = len(board.move_stack) + 1
    record = make_move_record(board, move, ply, game_id=game_id)
    board.push(move)
    return PlyResult(record=record, outcome=detect_terminal(board, player_color))


def apply_cpu_move(
    board: chess.Board,
    level: Level,
    *,
    game_seed: int,
    player_color: Color,
    book: OpeningBook | None = None,
    game_id: int | None = None,
) -> PlyResult:
    """Choose, apply and record the CPU's move; ``board`` is mutated."""
    ply = len(board.move_stack) + 1
    choice = choose_cpu_move(board, level, game_seed=game_seed, ply=ply, book=book)
    record = make_move_record(
        board,
        choice.move,
        ply,
        is_book=choice.is_book,
        cpu_meta=choice.meta,
        game_id=game_id,
    )
    board.push(choice.move)
    return PlyResult(record=record, outcome=detect_terminal(board, player_color), choice=choice)


def resolve_color(preferred: PreferredColor, seed: int) -> Color:
    """Resolve the player's colour; ``random`` is seeded, so replays match."""
    if preferred is PreferredColor.WHITE:
        return Color.WHITE
    if preferred is PreferredColor.BLACK:
        return Color.BLACK
    return Color.WHITE if random.Random(seed).random() < 0.5 else Color.BLACK


def derive_rated(source: GameSource, status: GameStatus, ply_count: int) -> bool:
    """FR-6 / DATA_MODEL: ``rated`` is derived at termination, never set by hand."""
    return (
        source is GameSource.PLAYED and status in DECISIVE_STATUSES and ply_count >= MIN_RATED_PLIES
    )


_PGN_RESULT: dict[GameStatus, str] = {
    GameStatus.DRAW: "1/2-1/2",
    GameStatus.IN_PROGRESS: "*",
    GameStatus.ABORTED: "*",
    GameStatus.UNFINISHED: "*",
}


def to_pgn(
    uci_moves: Sequence[str],
    *,
    player_color: Color,
    status: GameStatus,
    headers: dict[str, str] | None = None,
) -> str:
    """Export a game as valid PGN (FR-1); PGN is always derived, never stored."""
    game = chess.pgn.Game()
    for key, value in (headers or {}).items():
        game.headers[key] = value

    if status in _PGN_RESULT:
        result = _PGN_RESULT[status]
    elif status in DECISIVE_STATUSES:
        player_is_white = player_color is Color.WHITE
        player_won = status is GameStatus.PLAYER_WIN
        white_won = player_won == player_is_white
        result = "1-0" if white_won else "0-1"
    else:  # pragma: no cover - the mapping above is total over GameStatus
        result = "*"
    game.headers["Result"] = result

    node: chess.pgn.GameNode = game
    board = chess.Board()
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise IllegalMoveError(uci, sorted(board.san(m) for m in board.legal_moves))
        node = node.add_variation(move)
        board.push(move)

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    stream = io.StringIO()
    stream.write(game.accept(exporter))
    return stream.getvalue()
