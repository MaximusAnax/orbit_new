"""Shared, hermetic game-playing harness for the eval suite.

Used by M1a (adjacent separation), M9 (throttle fidelity, which reads M1a's
``cpu_meta`` corpus), M10 (end-to-end rating fidelity) and by the committed
FR-5 calibration generator.  Everything here is deterministic: seeds and level
configs in, move records out, no clock reads and no network.

*Adjudication* is standard engine-testing practice (fishtest/OpenBench) and is
specified in EVALS.md: the referee — the internal analyst at ``JUDGE_BUDGET``,
the one analyst configuration the whole suite uses — evaluates after each ply
from ply 60; ``|eval| >= 500`` cp for 4 consecutive plies is a win for the
leading side; at ply 160 the game is a draw unless ``|eval| > 150`` cp.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import chess

from chessmentor.constants import JUDGE_BUDGET
from chessmentor.engine.session import make_move_record
from chessmentor.engine.throttle import choose_cpu_move
from chessmentor.models import Level, MoveRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chessmentor.adapters.analyst import Analyst
    from chessmentor.adapters.book import OpeningBook

__all__ = [
    "ADJUDICATION_DRAW_CP",
    "ADJUDICATION_MAX_PLY",
    "ADJUDICATION_START_PLY",
    "ADJUDICATION_WIN_CP",
    "ADJUDICATION_WIN_PLIES",
    "LadderGame",
    "play_ladder_game",
]

#: Referee starts watching here (EVALS.md, M1a adjudication).
ADJUDICATION_START_PLY = 60
#: Sustained advantage that ends the game.
ADJUDICATION_WIN_CP = 500
#: How many consecutive plies the advantage must hold.
ADJUDICATION_WIN_PLIES = 4
#: Hard stop.
ADJUDICATION_MAX_PLY = 160
#: At the hard stop a game is a draw unless the referee sees more than this.
ADJUDICATION_DRAW_CP = 150


@dataclass(frozen=True)
class LadderGame:
    """One engine-vs-engine game and everything the metrics read off it."""

    white_level_id: int
    black_level_id: int
    seed: int
    opening_id: str
    #: White's score: 1.0 / 0.5 / 0.0.
    white_score: float
    plies: int
    termination: str
    records: tuple[MoveRecord, ...]
    uci_moves: tuple[str, ...]
    final_fen: str

    def score_for(self, level_id: int) -> float:
        """Score from ``level_id``'s point of view (mirror games are symmetric)."""
        if level_id == self.white_level_id:
            return self.white_score
        if level_id == self.black_level_id:
            return 1.0 - self.white_score
        raise ValueError(f"level {level_id} did not play this game")


def _referee_cp(board: chess.Board, analyst: Analyst) -> int:
    """Referee evaluation of ``board`` from White's point of view."""
    evaluation = analyst.analyse(board, node_budget=JUDGE_BUDGET)
    return evaluation.score_cp if board.turn == chess.WHITE else -evaluation.score_cp


def play_ladder_game(
    white_level: Level,
    black_level: Level,
    *,
    opening_uci: Sequence[str],
    opening_id: str,
    seed: int,
    book: OpeningBook | None = None,
    referee: Analyst | None = None,
    max_ply: int = ADJUDICATION_MAX_PLY,
) -> LadderGame:
    """Play one throttled CPU-vs-CPU game from a fixed opening, with adjudication.

    The opening plies are replayed onto the board first (they are fixture input,
    not engine output, and are recorded with ``is_book=True`` so the throttle
    metrics never count them).  Every subsequent ply goes through FR-4's single
    code path, so ``cpu_meta`` is exactly what production records.
    """
    board = chess.Board()
    records: list[MoveRecord] = []
    for uci in opening_uci:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"fixture opening {opening_id} is illegal at {uci}")
        records.append(make_move_record(board, move, len(board.move_stack) + 1, is_book=True))
        board.push(move)

    streak_side: int = 0  # +1 White leading, -1 Black leading, 0 none
    streak = 0
    white_score: float | None = None
    termination = "natural"

    while True:
        outcome = board.outcome(claim_draw=True)
        if outcome is not None:
            if outcome.winner is None:
                white_score = 0.5
            else:
                white_score = 1.0 if outcome.winner == chess.WHITE else 0.0
            termination = f"natural:{outcome.termination.name.lower()}"
            break

        ply = len(board.move_stack) + 1
        level = white_level if board.turn == chess.WHITE else black_level
        choice = choose_cpu_move(board, level, game_seed=seed, ply=ply, book=book)
        records.append(
            make_move_record(
                board, choice.move, ply, is_book=choice.is_book, cpu_meta=choice.meta
            )
        )
        board.push(choice.move)
        played = len(board.move_stack)

        if referee is not None and played >= ADJUDICATION_START_PLY:
            cp = _referee_cp(board, referee)
            side = 1 if cp > 0 else (-1 if cp < 0 else 0)
            if abs(cp) >= ADJUDICATION_WIN_CP and side != 0:
                streak = streak + 1 if side == streak_side else 1
                streak_side = side
            else:
                streak = 0
                streak_side = 0
            if streak >= ADJUDICATION_WIN_PLIES:
                white_score = 1.0 if streak_side > 0 else 0.0
                termination = "adjudicated:win"
                break
            if played >= max_ply:
                if abs(cp) > ADJUDICATION_DRAW_CP:
                    white_score = 1.0 if cp > 0 else 0.0
                    termination = "adjudicated:win_on_cap"
                else:
                    white_score = 0.5
                    termination = "adjudicated:draw"
                break
        elif played >= max_ply:
            # No referee configured: the cap is a draw by definition.
            white_score = 0.5
            termination = "capped:draw"
            break

    assert white_score is not None
    return LadderGame(
        white_level_id=white_level.id,
        black_level_id=black_level.id,
        seed=seed,
        opening_id=opening_id,
        white_score=white_score,
        plies=len(board.move_stack),
        termination=termination,
        records=tuple(records),
        uci_moves=tuple(m.uci() for m in board.move_stack),
        final_fen=board.fen(),
    )
