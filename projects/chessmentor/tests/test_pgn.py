"""FR-13 — PGN import: multi-game parsing, side resolution, result mapping."""

from __future__ import annotations

import pytest
from chessmentor.engine.pgn import (
    AmbiguousSideError,
    PgnParseError,
    import_outcome,
    parse_pgn,
    resolve_import_side,
)
from chessmentor.models import Color, GameStatus, Termination

SINGLE = """[Event "Casual"]
[White "Owner"]
[Black "Opponent"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

MULTI = (
    SINGLE
    + """
[Event "Casual"]
[White "Someone"]
[Black "Owner"]
[Result "0-1"]

1. d4 d5 2. c4 e6 0-1
"""
)

UNFINISHED = """[Event "Casual"]
[White "Owner"]
[Black "Opponent"]
[Result "*"]

1. e4 e5 *
"""

BOTH_TAGS = """[Event "Solo"]
[White "Owner"]
[Black "Owner"]
[Result "1/2-1/2"]

1. e4 e5 1/2-1/2
"""


def test_fr13_parses_a_single_game() -> None:
    games = parse_pgn(SINGLE)
    assert len(games) == 1
    assert games[0].uci_moves[:2] == ("e2e4", "e7e5")
    assert games[0].ply_count == 7
    assert games[0].tags["White"] == "Owner"
    assert games[0].tags["Result"] == "1-0"


def test_fr13_parses_multi_game_files() -> None:
    games = parse_pgn(MULTI)
    assert len(games) == 2
    assert games[1].tags["Black"] == "Owner"
    assert games[1].ply_count == 4


def test_fr13_empty_text_is_an_error() -> None:
    with pytest.raises(PgnParseError, match="no games"):
        parse_pgn("   \n\n")


def test_fr13_explicit_side_wins() -> None:
    tags = parse_pgn(SINGLE)[0].tags
    assert resolve_import_side(tags, requested=Color.BLACK, display_name="Owner") is Color.BLACK


def test_fr13_auto_matches_exactly_one_tag() -> None:
    tags = parse_pgn(SINGLE)[0].tags
    assert resolve_import_side(tags, requested=None, display_name="Owner") is Color.WHITE
    assert resolve_import_side(tags, requested=None, display_name="opponent") is Color.BLACK


def test_fr13_auto_rejects_zero_matches() -> None:
    tags = parse_pgn(SINGLE)[0].tags
    with pytest.raises(AmbiguousSideError, match="--as") as info:
        resolve_import_side(tags, requested=None, display_name="Somebody Else")
    assert info.value.matches == []


def test_fr13_auto_rejects_two_matches() -> None:
    tags = parse_pgn(BOTH_TAGS)[0].tags
    with pytest.raises(AmbiguousSideError, match="--as") as info:
        resolve_import_side(tags, requested=None, display_name="Owner")
    assert info.value.matches == ["White", "Black"]


def test_fr13_auto_rejects_an_empty_display_name() -> None:
    tags = parse_pgn(SINGLE)[0].tags
    with pytest.raises(AmbiguousSideError):
        resolve_import_side(tags, requested=None, display_name="   ")


def test_fr13_result_maps_to_status_from_the_player_side() -> None:
    white = import_outcome("1-0", Color.WHITE)
    assert white.status is GameStatus.PLAYER_WIN
    assert white.result_score == 1.0
    assert white.termination is Termination.IMPORTED_RESULT

    black = import_outcome("1-0", Color.BLACK)
    assert black.status is GameStatus.OPPONENT_WIN
    assert black.result_score == 0.0

    drawn = import_outcome("1/2-1/2", Color.BLACK)
    assert drawn.status is GameStatus.DRAW
    assert drawn.result_score == 0.5


def test_fr13_unparseable_results_become_unfinished() -> None:
    for tag in ("*", None, "", "garbage"):
        outcome = import_outcome(tag, Color.WHITE)
        assert outcome.status is GameStatus.UNFINISHED
        assert outcome.termination is Termination.IMPORTED_UNFINISHED
        assert outcome.result_score is None


def test_fr13_unfinished_game_still_parses() -> None:
    games = parse_pgn(UNFINISHED)
    assert games[0].ply_count == 2
    outcome = import_outcome(games[0].tags.get("Result"), Color.WHITE)
    assert outcome.status is GameStatus.UNFINISHED
