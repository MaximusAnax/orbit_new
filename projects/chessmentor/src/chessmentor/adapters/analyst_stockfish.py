"""Live ``Analyst``: a UCI engine driven through ``chess.engine``.

Activates only when ``CHESSMENTOR_STOCKFISH_PATH`` points at an executable.  It
is never on the eval or test runtime path (SCOPE non-goal 8) and this module is
never imported by the offline path — importing it costs nothing, but
constructing :class:`StockfishAnalyst` without the binary raises
:class:`AnalystUnavailableError`.

No pip dependency is added: ``chess.engine`` ships with python-chess.  The
import is done lazily so that a broken asyncio environment cannot affect the
offline path.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

import chess

from ..constants import DEEP_BUDGET, MATE_SCORE
from ..models import AnalystKind, MoveEval
from .analyst import AnalystUnavailableError

__all__ = ["STOCKFISH_PATH_ENV", "StockfishAnalyst"]

STOCKFISH_PATH_ENV = "CHESSMENTOR_STOCKFISH_PATH"


def _resolve_path(explicit: str | None) -> str:
    candidate = explicit or os.environ.get(STOCKFISH_PATH_ENV)
    if not candidate:
        raise AnalystUnavailableError(
            f"no UCI engine configured: set {STOCKFISH_PATH_ENV} to a Stockfish binary"
        )
    resolved = shutil.which(candidate) or candidate
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise AnalystUnavailableError(f"{STOCKFISH_PATH_ENV}={candidate!r} is not an executable")
    return resolved


def _to_internal_cp(score: Any) -> int:
    """Map a python-chess ``Score`` onto our ``+/-(MATE_SCORE - ply)`` encoding."""
    mate = score.mate()
    if mate is not None:
        plies = max(1, 2 * abs(mate) - 1)
        magnitude = MATE_SCORE - plies
        return magnitude if mate > 0 else -magnitude
    cp = score.score()
    return int(cp) if cp is not None else 0


class StockfishAnalyst:
    """Deeper, on-demand analysis from a configured UCI engine."""

    def __init__(self, path: str | None = None) -> None:
        self._path = _resolve_path(path)
        try:
            import chess.engine as chess_engine
        except ImportError as exc:  # pragma: no cover - python-chess always ships it
            raise AnalystUnavailableError("chess.engine is unavailable") from exc
        self._engine_module = chess_engine
        self._engine = chess_engine.SimpleEngine.popen_uci(self._path)
        self._version = str(self._engine.id.get("name", "stockfish"))

    @property
    def kind(self) -> AnalystKind:
        return AnalystKind.STOCKFISH

    @property
    def version(self) -> str:
        return self._version

    def analyse(self, board: chess.Board, *, node_budget: int = DEEP_BUDGET) -> MoveEval:
        if board.is_game_over(claim_draw=False):
            return MoveEval(best_move=None, score_cp=0, pv=[], nodes=0, depth=0)
        limit = self._engine_module.Limit(nodes=node_budget)
        info = self._engine.analyse(board, limit)
        pv_moves = list(info.get("pv") or [])
        score = info.get("score")
        score_cp = _to_internal_cp(score.pov(board.turn)) if score is not None else 0
        return MoveEval(
            best_move=pv_moves[0].uci() if pv_moves else None,
            score_cp=score_cp,
            pv=[m.uci() for m in pv_moves[:6]],
            nodes=int(info.get("nodes", 0) or 0),
            depth=int(info.get("depth", 0) or 0),
        )

    def close(self) -> None:
        self._engine.quit()

    def __enter__(self) -> StockfishAnalyst:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
