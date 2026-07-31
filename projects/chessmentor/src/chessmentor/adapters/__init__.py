"""Provider interfaces plus their offline (default) and live implementations.

Offline implementations are what tests and evals exercise.  Live implementations
(``analyst_stockfish``, ``book_lichess``) are imported lazily by
:func:`live_analyst` / :func:`live_book` so the offline path never touches them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .analyst import Analyst, AnalystUnavailableError
from .analyst_internal import InternalAnalyst
from .book import OpeningBook
from .book_committed import BookValidationError, CommittedBook

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

__all__ = [
    "Analyst",
    "AnalystUnavailableError",
    "InternalAnalyst",
    "OpeningBook",
    "CommittedBook",
    "BookValidationError",
    "live_analyst",
    "live_book",
]


def live_analyst(path: str | None = None) -> Analyst:
    """Construct the live UCI analyst; raises when no binary is configured."""
    from .analyst_stockfish import StockfishAnalyst

    return StockfishAnalyst(path)


def live_book(fallback: OpeningBook) -> OpeningBook:
    """Construct the live opening explorer; raises when it is not enabled."""
    from .book_lichess import LichessExplorerBook

    return LichessExplorerBook(fallback)
