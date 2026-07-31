"""``OpeningBook`` — provider interface for opening knowledge.

Two capabilities:

``probe(board)``
    the weighted continuations the CPU may play from ``board`` (FR-4 step 1).
``identify(moves)``
    the longest committed line that prefixes ``moves`` — its length is the
    game's ``book_depth``, which drives the FR-9 ACPL exclusion rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import chess

from ..models import BookMove, Opening

__all__ = ["OpeningBook"]


@runtime_checkable
class OpeningBook(Protocol):
    """Provider interface for the opening book."""

    @property
    def name(self) -> str:
        """Short identifier of the implementation, for provenance."""
        ...

    def probe(self, board: chess.Board) -> list[BookMove]:
        """Weighted book continuations from ``board``.

        ``board`` must have been built by replaying moves from the standard
        starting position; implementations read ``board.move_stack``.  Returns an
        empty list when the position is out of book.
        """
        ...

    def identify(self, moves: Sequence[str]) -> Opening | None:
        """Longest-prefix match of the UCI move list against the book."""
        ...
