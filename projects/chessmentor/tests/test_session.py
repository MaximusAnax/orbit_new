"""FR-1 / FR-6 — rules, notation, termination and pure game flow."""

from __future__ import annotations

import chess
import pytest
from chessmentor.constants import MIN_RATED_PLIES
from chessmentor.engine.session import (
    IllegalMoveError,
    abort_outcome,
    apply_cpu_move,
    apply_player_move,
    board_from_moves,
    derive_rated,
    detect_terminal,
    make_move_record,
    parse_move,
    resignation_outcome,
    resolve_color,
    to_pgn,
)
from chessmentor.models import (
    Color,
    GameSource,
    GameStatus,
    PreferredColor,
    Termination,
)

SCHOLARS_MATE = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


# --- FR-1: notation and legality ------------------------------------------------ #


def test_fr1_san_and_uci_are_both_accepted() -> None:
    board = chess.Board()
    assert parse_move(board, "e4") == chess.Move.from_uci("e2e4")
    assert parse_move(board, "e2e4") == chess.Move.from_uci("e2e4")
    assert parse_move(board, " Nf3 ") == chess.Move.from_uci("g1f3")


def test_fr1_illegal_moves_are_rejected_with_the_legal_list() -> None:
    board = chess.Board()
    with pytest.raises(IllegalMoveError) as info:
        parse_move(board, "e5")
    assert "e4" in info.value.legal_san
    assert len(info.value.legal_san) == 20


def test_fr1_nonsense_input_is_rejected() -> None:
    board = chess.Board()
    with pytest.raises(IllegalMoveError):
        parse_move(board, "not-a-move")


def test_fr1_board_from_moves_validates_the_whole_line() -> None:
    board = board_from_moves(SCHOLARS_MATE)
    assert board.is_checkmate()
    with pytest.raises(IllegalMoveError):
        board_from_moves(["e2e4", "e2e4"])


# --- FR-1: terminal detection ---------------------------------------------------- #


def test_fr1_checkmate_is_detected_from_both_perspectives() -> None:
    board = board_from_moves(SCHOLARS_MATE)
    as_white = detect_terminal(board, Color.WHITE)
    assert as_white is not None
    assert as_white.status is GameStatus.PLAYER_WIN
    assert as_white.termination is Termination.CHECKMATE
    assert as_white.result_score == 1.0

    as_black = detect_terminal(board, Color.BLACK)
    assert as_black is not None
    assert as_black.status is GameStatus.OPPONENT_WIN
    assert as_black.result_score == 0.0


def test_fr1_stalemate_is_a_draw() -> None:
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    outcome = detect_terminal(board, Color.BLACK)
    assert outcome is not None
    assert outcome.termination is Termination.STALEMATE
    assert outcome.status is GameStatus.DRAW
    assert outcome.result_score == 0.5


def test_fr1_insufficient_material_is_a_draw() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/4KB2 w - - 0 1")
    outcome = detect_terminal(board, Color.WHITE)
    assert outcome is not None
    assert outcome.termination is Termination.INSUFFICIENT_MATERIAL


def test_fr1_fifty_move_rule_ends_the_game_automatically() -> None:
    """D15: claimable draws are applied, not modelled as claims."""
    board = chess.Board("4k3/8/8/8/8/8/R7/4K3 w - - 99 80")
    board.push(chess.Move.from_uci("a2a3"))
    outcome = detect_terminal(board, Color.WHITE)
    assert outcome is not None
    assert outcome.termination is Termination.FIFTY_MOVE_RULE
    assert outcome.status is GameStatus.DRAW


def test_fr1_threefold_repetition_ends_the_game_automatically() -> None:
    board = chess.Board()
    for _ in range(2):
        for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
            board.push(chess.Move.from_uci(uci))
    outcome = detect_terminal(board, Color.WHITE)
    assert outcome is not None
    assert outcome.termination is Termination.THREEFOLD_REPETITION
    assert outcome.status is GameStatus.DRAW


def test_fr1_an_ongoing_game_has_no_terminal_state() -> None:
    assert detect_terminal(chess.Board(), Color.WHITE) is None


# --- FR-6: resignation, abort, rating eligibility ---------------------------------- #


def test_fr6_resignation_loses_for_whoever_resigns() -> None:
    mine = resignation_outcome(Color.WHITE, Color.WHITE)
    assert mine.status is GameStatus.OPPONENT_WIN
    assert mine.result_score == 0.0
    theirs = resignation_outcome(Color.BLACK, Color.WHITE)
    assert theirs.status is GameStatus.PLAYER_WIN
    assert theirs.result_score == 1.0


def test_fr6_abort_is_only_allowed_before_ply_eight() -> None:
    outcome = abort_outcome(5)
    assert outcome.status is GameStatus.ABORTED
    assert outcome.result_score is None
    with pytest.raises(ValueError, match="aborted"):
        abort_outcome(MIN_RATED_PLIES)


def test_fr6_rated_is_derived_not_set() -> None:
    assert derive_rated(GameSource.PLAYED, GameStatus.PLAYER_WIN, 40)
    assert derive_rated(GameSource.PLAYED, GameStatus.DRAW, MIN_RATED_PLIES)
    assert not derive_rated(GameSource.PLAYED, GameStatus.PLAYER_WIN, MIN_RATED_PLIES - 1)
    assert not derive_rated(GameSource.PLAYED, GameStatus.ABORTED, 40)
    assert not derive_rated(GameSource.IMPORTED, GameStatus.PLAYER_WIN, 40)


# --- FR-6: ply application -------------------------------------------------------- #


def test_fr6_player_move_records_san_uci_and_fen() -> None:
    board = chess.Board()
    result = apply_player_move(board, "e4", player_color=Color.WHITE)
    assert result.record.ply == 1
    assert result.record.color is Color.WHITE
    assert result.record.san == "e4"
    assert result.record.uci == "e2e4"
    assert result.record.fen_after == board.fen()
    assert result.record.cpu_meta is None
    assert result.outcome is None


def test_fr6_cpu_move_records_throttle_metadata(levels, book) -> None:
    board = chess.Board()
    result = apply_cpu_move(board, levels[0], game_seed=42, player_color=Color.BLACK, book=book)
    assert result.record.ply == 1
    assert result.record.cpu_meta is not None
    assert result.choice is not None
    assert result.record.uci == result.choice.move.uci()


def test_fr6_replaying_the_same_seed_reproduces_the_cpu(levels, book) -> None:
    """FR-6 / FR-16: same seed + same player moves ⇒ identical CPU moves."""

    def play() -> list[str]:
        board = chess.Board()
        moves: list[str] = []
        player_script = ["e4", "Nf3", "Bc4", "d3", "O-O", "Nc3"]
        for text in player_script:
            try:
                moves.append(apply_player_move(board, text, player_color=Color.WHITE).record.uci)
            except IllegalMoveError:
                break
            if detect_terminal(board, Color.WHITE):
                break
            reply = apply_cpu_move(
                board, levels[3], game_seed=987654321, player_color=Color.WHITE, book=book
            )
            moves.append(reply.record.uci)
            if reply.outcome:
                break
        return moves

    assert play() == play()


def test_fr6_move_records_alternate_colours() -> None:
    board = chess.Board()
    first = make_move_record(board, chess.Move.from_uci("e2e4"), 1)
    board.push(chess.Move.from_uci("e2e4"))
    second = make_move_record(board, chess.Move.from_uci("e7e5"), 2)
    assert first.color is Color.WHITE
    assert second.color is Color.BLACK


def test_fr6_colour_resolution_is_seeded() -> None:
    assert resolve_color(PreferredColor.WHITE, 1) is Color.WHITE
    assert resolve_color(PreferredColor.BLACK, 1) is Color.BLACK
    picks = {resolve_color(PreferredColor.RANDOM, seed) for seed in range(20)}
    assert picks == {Color.WHITE, Color.BLACK}
    assert resolve_color(PreferredColor.RANDOM, 7) is resolve_color(PreferredColor.RANDOM, 7)


# --- FR-1: PGN export ---------------------------------------------------------------- #


def test_fr1_pgn_export_round_trips() -> None:
    import io

    import chess.pgn

    text = to_pgn(
        SCHOLARS_MATE,
        player_color=Color.WHITE,
        status=GameStatus.PLAYER_WIN,
        headers={"Event": "ChessMentor", "White": "Owner", "Black": "L4"},
    )
    game = chess.pgn.read_game(io.StringIO(text))
    assert game is not None
    assert game.headers["Result"] == "1-0"
    assert game.headers["White"] == "Owner"
    assert [m.uci() for m in game.mainline_moves()] == SCHOLARS_MATE


def test_fr1_pgn_result_follows_the_player_colour() -> None:
    as_black = to_pgn(SCHOLARS_MATE, player_color=Color.BLACK, status=GameStatus.OPPONENT_WIN)
    assert 'Result "1-0"' in as_black
    draw = to_pgn(SCHOLARS_MATE, player_color=Color.WHITE, status=GameStatus.DRAW)
    assert 'Result "1/2-1/2"' in draw
    open_game = to_pgn(["e2e4"], player_color=Color.WHITE, status=GameStatus.IN_PROGRESS)
    assert 'Result "*"' in open_game


def test_fr1_pgn_rejects_an_illegal_line() -> None:
    with pytest.raises(IllegalMoveError):
        to_pgn(["e2e4", "e2e4"], player_color=Color.WHITE, status=GameStatus.IN_PROGRESS)
