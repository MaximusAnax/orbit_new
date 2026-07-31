"""FR-13 — PGN import rules.

Parsing single- or multi-game PGN text (python-chess), plus the two decisions
the API/CLI must never make on their own:

* **side selection** — ``--as white|black`` is explicit; ``--as auto`` matches
  the profile ``display_name`` case-insensitively against the White/Black tags.
  **Exactly one** match selects that side; zero or two matches is a hard error
  naming ``--as`` (API 422) — never a silent guess.
* **result mapping** — a ``Result`` tag of ``*`` or anything unparseable yields
  status ``unfinished`` with ``result_score = null``: analysable, never rated.

Imported games are never rated and never affect level selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from io import StringIO

import chess
import chess.pgn

from ..models import Color, GameStatus, Termination

__all__ = [
    "AmbiguousSideError",
    "ImportResult",
    "ParsedGame",
    "PgnParseError",
    "import_outcome",
    "parse_pgn",
    "resolve_import_side",
]


class PgnParseError(ValueError):
    """The PGN text could not be parsed into at least one game."""


class AmbiguousSideError(ValueError):
    """``--as auto`` matched zero or two tags; the caller must pass ``--as``."""

    def __init__(self, matches: Sequence[str], display_name: str) -> None:
        super().__init__(
            f"display name {display_name!r} matched {len(matches)} of the White/Black tags "
            f"({', '.join(matches) or 'none'}); pass --as white|black explicitly"
        )
        self.matches = list(matches)
        self.display_name = display_name


@dataclass(frozen=True)
class ParsedGame:
    """One game extracted from a PGN file."""

    uci_moves: tuple[str, ...]
    tags: dict[str, str] = field(default_factory=dict)
    final_fen: str = chess.STARTING_FEN

    @property
    def ply_count(self) -> int:
        return len(self.uci_moves)


@dataclass(frozen=True)
class ImportResult:
    """Status/termination/score derived from a PGN ``Result`` tag."""

    status: GameStatus
    termination: Termination
    result_score: float | None


def parse_pgn(text: str) -> list[ParsedGame]:
    """Parse every game in ``text``; raises when none can be read."""
    stream = StringIO(text)
    games: list[ParsedGame] = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        board = game.board()
        ucis: list[str] = []
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise PgnParseError(
                    f"illegal move {move.uci()} in game {len(games) + 1} of the PGN"
                )
            ucis.append(move.uci())
            board.push(move)
        games.append(
            ParsedGame(
                uci_moves=tuple(ucis),
                tags={key: str(value) for key, value in game.headers.items()},
                final_fen=board.fen(),
            )
        )
    if not games:
        raise PgnParseError("no games found in the PGN text")
    return games


def resolve_import_side(
    tags: dict[str, str],
    *,
    requested: Color | None,
    display_name: str,
) -> Color:
    """Explicit side wins; otherwise exactly one tag must match ``display_name``."""
    if requested is not None:
        return requested
    needle = display_name.strip().casefold()
    if not needle:
        raise AmbiguousSideError([], display_name)
    matches: list[str] = []
    if tags.get("White", "").strip().casefold() == needle:
        matches.append("White")
    if tags.get("Black", "").strip().casefold() == needle:
        matches.append("Black")
    if len(matches) != 1:
        raise AmbiguousSideError(matches, display_name)
    return Color.WHITE if matches[0] == "White" else Color.BLACK


def import_outcome(result_tag: str | None, player_color: Color) -> ImportResult:
    """Map a PGN ``Result`` tag onto our status/termination/score triple."""
    normalized = (result_tag or "*").strip()
    if normalized == "1/2-1/2":
        return ImportResult(GameStatus.DRAW, Termination.IMPORTED_RESULT, 0.5)
    if normalized in ("1-0", "0-1"):
        white_won = normalized == "1-0"
        player_is_white = player_color is Color.WHITE
        if white_won == player_is_white:
            return ImportResult(GameStatus.PLAYER_WIN, Termination.IMPORTED_RESULT, 1.0)
        return ImportResult(GameStatus.OPPONENT_WIN, Termination.IMPORTED_RESULT, 0.0)
    return ImportResult(GameStatus.UNFINISHED, Termination.IMPORTED_UNFINISHED, None)
