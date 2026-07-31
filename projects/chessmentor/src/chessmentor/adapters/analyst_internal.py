"""Offline ``Analyst``: the project's own search with the throttle switched off.

This is the default implementation used by tests, evals and the runtime judge
pass: ``noise_sigma_cp = 0``, ``blunder_prob = 0``, ``max_depth = 6``, budget
supplied by the caller (``JUDGE_BUDGET`` by default).  It is fully deterministic
per FR-2's per-position contract.
"""

from __future__ import annotations

import chess

from .. import __version__
from ..constants import JUDGE_BUDGET
from ..engine.search import SearchConfig, search
from ..models import AnalystKind, MoveEval

__all__ = ["InternalAnalyst", "ANALYST_MAX_DEPTH"]

#: FR-4's throttle is *not* applied here; only the depth ceiling is.
ANALYST_MAX_DEPTH = 6

#: Budget spent extending a one-move PV by the opponent's reply (see below).
_PV_EXTENSION_BUDGET = 256


class InternalAnalyst:
    """The internal engine used as an analyst."""

    def __init__(self, *, max_depth: int = ANALYST_MAX_DEPTH, version: str | None = None) -> None:
        self._config = SearchConfig(max_depth=max_depth)
        self._version = version or __version__

    @property
    def kind(self) -> AnalystKind:
        return AnalystKind.INTERNAL

    @property
    def version(self) -> str:
        return self._version

    @property
    def max_depth(self) -> int:
        return self._config.max_depth

    def analyse(self, board: chess.Board, *, node_budget: int = JUDGE_BUDGET) -> MoveEval:
        result = search(board, self._config, node_budget)
        if result.best_move is None:
            # Terminal position: no move, score is the game result from the
            # side to move (mate encoded at ply 0, stalemate 0).
            return MoveEval(best_move=None, score_cp=result.best_score_cp, pv=[], nodes=0, depth=0)

        pv = list(result.pv)
        nodes = result.nodes
        if len(pv) < 2:
            # FR-9/US-5 want a >= 2-ply line wherever one exists.  A single-move
            # PV only happens when the budget barely covered depth 1, so spend a
            # small, fixed, deterministic extension on the opponent's reply.
            probe = board.copy(stack=False)
            probe.push(pv[0])
            if probe.legal_moves:
                reply = search(probe, SearchConfig(max_depth=1), _PV_EXTENSION_BUDGET)
                nodes += reply.nodes
                if reply.best_move is not None:
                    pv = [*pv, reply.best_move]

        return MoveEval(
            best_move=result.best_move.uci(),
            score_cp=result.best_score_cp,
            pv=[m.uci() for m in pv],
            nodes=nodes,
            depth=result.depth,
        )
