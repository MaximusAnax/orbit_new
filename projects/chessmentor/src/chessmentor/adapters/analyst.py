"""``Analyst`` — provider interface for position evaluation.

One method: evaluate a position under an explicit per-move node budget and
report the best move, its score (centipawns from the side to move), the
principal variation and the nodes spent.

Every consumer passes the budget explicitly; ``JUDGE_BUDGET`` is the one
configuration the runtime judge pass and every eval metric use.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import chess

from ..models import AnalystKind, MoveEval

__all__ = ["Analyst", "AnalystUnavailableError"]


class AnalystUnavailableError(RuntimeError):
    """A live analyst was requested but its binary/credentials are absent."""


@runtime_checkable
class Analyst(Protocol):
    """Provider interface for position analysis."""

    @property
    def kind(self) -> AnalystKind:
        """Which analyst family this is — recorded on every ``GameAnalysis``."""
        ...

    @property
    def version(self) -> str:
        """Version string pinning the code that produced an analysis."""
        ...

    def analyse(self, board: chess.Board, *, node_budget: int) -> MoveEval:
        """Evaluate ``board`` under ``node_budget`` nodes per move."""
        ...
