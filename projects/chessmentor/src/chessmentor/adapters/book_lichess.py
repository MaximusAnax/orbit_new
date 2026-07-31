"""Live ``OpeningBook``: the Lichess opening-explorer API.

Activates only when ``CHESSMENTOR_LICHESS_LIVE=1``.  It *enriches* the CPU's
book variety (``probe``); ECO naming (``identify``) stays with the committed
book so coaching text never depends on a network round trip and so book depth —
which drives the FR-9 ACPL exclusion — stays reproducible.

Never used by tests or evals (hermeticity, FR-16): constructing it without the
environment flag raises.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

import chess

from ..constants import MAX_BOOK_DEPTH
from ..models import BookMove, Opening
from .book import OpeningBook

__all__ = ["LichessExplorerBook", "LICHESS_LIVE_ENV", "BookUnavailableError"]

LICHESS_LIVE_ENV = "CHESSMENTOR_LICHESS_LIVE"
_EXPLORER_URL = "https://explorer.lichess.ovh/masters"


class BookUnavailableError(RuntimeError):
    """The live book was requested but is not enabled."""


class LichessExplorerBook:
    """Opening-explorer-backed continuations with a committed-book fallback."""

    def __init__(
        self,
        fallback: OpeningBook,
        *,
        timeout: float = 5.0,
        top_moves: int = 8,
    ) -> None:
        if os.environ.get(LICHESS_LIVE_ENV) != "1":
            raise BookUnavailableError(
                f"live opening explorer is disabled: set {LICHESS_LIVE_ENV}=1 to enable it"
            )
        self._fallback = fallback
        self._timeout = timeout
        self._top_moves = top_moves

    @property
    def name(self) -> str:
        return "lichess-explorer"

    def probe(self, board: chess.Board) -> list[BookMove]:
        if board.root().fen() != chess.STARTING_FEN:
            return []
        ucis = [m.uci() for m in board.move_stack]
        if len(ucis) > MAX_BOOK_DEPTH:
            return []
        try:
            payload = self._fetch(ucis)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            # The live book is an enrichment; a network failure degrades to the
            # committed book rather than stalling a game.
            return self._fallback.probe(board)

        moves: list[BookMove] = []
        for entry in payload.get("moves", [])[: self._top_moves]:
            uci = entry.get("uci")
            games = int(entry.get("white", 0)) + int(entry.get("draws", 0)) + int(
                entry.get("black", 0)
            )
            if uci and games > 0:
                moves.append(BookMove(uci=uci, weight=games))
        return moves or self._fallback.probe(board)

    def identify(self, moves: Sequence[str]) -> Opening | None:
        # ECO naming and book depth stay committed and reproducible.
        return self._fallback.identify(moves)

    def _fetch(self, ucis: Sequence[str]) -> dict:
        query = urllib.parse.urlencode({"play": ",".join(ucis), "topGames": 0, "recentGames": 0})
        request = urllib.request.Request(  # noqa: S310 - fixed https host
            f"{_EXPLORER_URL}?{query}",
            headers={"Accept": "application/json", "User-Agent": "chessmentor/0.1"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
