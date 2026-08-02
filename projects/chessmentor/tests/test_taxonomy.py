"""FR-11 — SEE, motif detectors and the precedence classifier."""

from __future__ import annotations

import chess
import pytest
from chessmentor.constants import MATE_SCORE
from chessmentor.engine.taxonomy import (
    MoveContext,
    classify,
    detect_fork,
    detect_pin_or_skewer,
    least_valuable_attacker,
    piece_move_counts,
    static_exchange_evaluation,
)
from chessmentor.models import MistakeCategory, Motif, Phase


def _see(fen: str, uci: str) -> int:
    board = chess.Board(fen)
    return static_exchange_evaluation(board, chess.Move.from_uci(uci))


# --- static exchange evaluation ---------------------------------------------- #


def test_fr11_see_free_capture_wins_the_piece() -> None:
    # White rook takes an undefended black knight on d5.
    assert _see("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1", "d1d5") == 320


def test_fr11_see_defended_capture_loses_the_exchange() -> None:
    # Rxd5 is met by …exd5 (the e6 pawn defends d5): rook (500) for knight (320).
    value = _see("4k3/8/4p3/3n4/8/8/8/3RK3 w - - 0 1", "d1d5")
    assert value == 320 - 500


def test_fr11_see_equal_trade_is_zero() -> None:
    # Nxd5 answered by …exd5: knight for knight.
    assert _see("4k3/8/4p3/3n4/8/4N3/8/4K3 w - - 0 1", "e3d5") == 0


def test_fr11_see_counts_the_whole_stack_with_xrays() -> None:
    # Two white rooks doubled on the d-file take a defended pawn on d5.
    value = _see("3rk3/8/8/3p4/8/8/3R4/3RK3 w - - 0 1", "d2d5")
    assert value == 100


def test_fr11_see_of_a_quiet_move_measures_the_reply() -> None:
    # Nb1-c3?? walks onto a square attacked by a pawn and defended by nothing:
    # a quiet move's SEE is what the opponent wins on the arrival square.
    board = chess.Board("4k3/8/8/8/1p6/8/8/1N2K3 w - - 0 1")
    quiet = chess.Move.from_uci("b1c3")
    assert quiet in board.legal_moves
    assert static_exchange_evaluation(board, quiet) == -320
    # …while a safe square costs nothing.
    assert static_exchange_evaluation(board, chess.Move.from_uci("b1d2")) == 0


def test_fr11_see_handles_en_passant() -> None:
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    move = chess.Move.from_uci("e5d6")
    assert board.is_en_passant(move)
    assert static_exchange_evaluation(board, move) == 100


def test_fr11_see_rejects_a_move_from_an_empty_square() -> None:
    board = chess.Board()
    with pytest.raises(ValueError, match="does not move a piece"):
        static_exchange_evaluation(board, chess.Move.from_uci("d4d5"))


def test_fr11_least_valuable_attacker_prefers_the_cheapest() -> None:
    board = chess.Board("4k3/8/8/3p4/2P1N3/8/8/3RK3 w - - 0 1")
    square, piece_type = least_valuable_attacker(board, board.occupied, chess.D5, chess.WHITE)
    assert piece_type is chess.PAWN
    assert square == chess.C4


# --- motif detectors ----------------------------------------------------------- #


def test_fr11_fork_detects_a_knight_hitting_two_majors() -> None:
    # Nd6 attacks the rooks on c8 and e8.
    board = chess.Board("2r1r3/8/8/8/4N3/8/8/K6k w - - 0 1")
    board.push(chess.Move.from_uci("e4d6"))
    facts = detect_fork(board, chess.D6, chess.WHITE)
    assert facts is not None
    assert facts["motif"] == "fork"
    assert len(facts["targets"]) >= 2


def test_fr11_fork_counts_king_plus_any_piece() -> None:
    # Nd6+ hits the king on e8 and the rook on c8.
    board = chess.Board("2r1k3/8/8/8/4N3/8/8/4K3 w - - 0 1")
    board.push(chess.Move.from_uci("e4d6"))
    facts = detect_fork(board, chess.D6, chess.WHITE)
    assert facts is not None
    assert facts["motif"] == "fork"


def test_fr11_fork_ignores_a_single_target() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/3RK3 w - - 0 1")
    board.push(chess.Move.from_uci("d1d8"))
    assert detect_fork(board, chess.D8, chess.WHITE) is None


def test_fr11_pin_is_detected_when_the_front_piece_is_cheaper() -> None:
    # Ra1-d1 newly lines the rook up through Nd6 onto Qd8.
    before = chess.Board("3qk3/8/3n4/8/8/8/8/R3K3 w - - 0 1")
    move = chess.Move.from_uci("a1d1")
    after = before.copy(stack=False)
    after.push(move)
    facts = detect_pin_or_skewer(before, after, move, chess.WHITE)
    assert facts is not None
    assert facts["motif"] == "pin"
    assert facts["front"] == "d6"
    assert facts["back"] == "d8"
    assert facts["front_value"] < facts["back_value"]


def test_fr11_skewer_is_detected_when_the_front_piece_is_dearer() -> None:
    # Ra1-d1 newly lines the rook up through Qd6 onto Nd8.
    before = chess.Board("3nk3/8/3q4/8/8/8/8/R3K3 w - - 0 1")
    move = chess.Move.from_uci("a1d1")
    after = before.copy(stack=False)
    after.push(move)
    facts = detect_pin_or_skewer(before, after, move, chess.WHITE)
    assert facts is not None
    assert facts["motif"] == "skewer"


def test_fr11_alignment_must_be_new() -> None:
    """A slider already lined up on the same pair is not "newly aligned"."""
    before = chess.Board("3qk3/8/3n4/8/8/8/8/3RK3 w - - 0 1")
    move = chess.Move.from_uci("d1d2")
    after = before.copy(stack=False)
    after.push(move)
    assert detect_pin_or_skewer(before, after, move, chess.WHITE) is None


# --- piece-instance history ----------------------------------------------------- #


def test_fr11_piece_move_counts_follow_a_piece_around() -> None:
    squares, counts = piece_move_counts(["g1f3", "g8f6", "f3e5", "f6e4", "e5f3"])
    knight = squares[chess.F3]
    assert counts[knight] == 3
    assert knight == chess.G1


def test_fr11_piece_move_counts_survive_captures_and_castling() -> None:
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "e1g1"]
    squares, counts = piece_move_counts(moves)
    assert squares[chess.G1] == chess.E1  # the king reached g1
    assert squares[chess.F1] == chess.H1  # the rook reached f1
    assert counts[chess.E1] == 1
    assert counts[chess.H1] == 1


# --- precedence classifier -------------------------------------------------------- #


def _context(fen: str, uci: str, **overrides: object) -> MoveContext:
    board = chess.Board(fen)
    base: dict[str, object] = {
        "board_before": board,
        "move": chess.Move.from_uci(uci),
        "player_color": board.turn,
        "phase": Phase.MIDDLEGAME,
        "cp_best": 0,
        "cp_played": -300,
        "w_before": 0.5,
        "w_after": 0.2,
        "best_uci": None,
        "best_pv": (),
        "reply_pv": (),
        "prior_moves": (),
    }
    base.update(overrides)
    return MoveContext(**base)  # type: ignore[arg-type]


def test_fr11_rule1_allowed_mate_wins_precedence() -> None:
    result = classify(
        _context(
            "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1",
            "e1g1",
            cp_best=20,
            cp_played=-(MATE_SCORE - 3),
            reply_pv=("c6d4",),
        )
    )
    assert result.category is MistakeCategory.ALLOWED_MATE
    assert result.evidence["rule"] == "allowed_mate"


def test_fr11_rule2_missed_mate() -> None:
    result = classify(
        _context(
            "7k/6pp/8/8/8/8/8/R6K w - - 0 1",
            "a1a2",
            cp_best=MATE_SCORE - 1,
            cp_played=50,
            best_pv=("a1a8",),
        )
    )
    assert result.category is MistakeCategory.MISSED_MATE
    assert result.evidence["cp_best"] == MATE_SCORE - 1


def test_fr11_rule3_bad_trade_beats_hung_piece() -> None:
    """The documented precedence case: a losing capture that also hangs the capturer."""
    result = classify(
        _context(
            "4k3/8/4p3/3n4/8/8/8/3RK3 w - - 0 1",
            "d1d5",
            cp_best=0,
            cp_played=-200,
            reply_pv=("e6d5",),
        )
    )
    assert result.category is MistakeCategory.BAD_TRADE
    assert result.evidence["see_cp"] <= -100


def test_fr11_rule4_hung_piece_needs_a_newly_possible_capture() -> None:
    # White plays Nb1-c3?? where a black pawn on b4 can take it for free.
    result = classify(
        _context(
            "4k3/8/8/8/1p6/8/8/1N2K3 w - - 0 1",
            "b1c3",
            cp_best=0,
            cp_played=-320,
            reply_pv=("b4c3",),
        )
    )
    assert result.category is MistakeCategory.HUNG_PIECE
    assert result.evidence["capture_uci"] == "b4c3"
    assert result.evidence["see_cp"] >= 100
    # The capture did not exist before the move.
    assert result.evidence["pre_move_see_cp"] is None


def test_fr11_rule5_missed_tactic_requires_material_in_the_pv() -> None:
    result = classify(
        _context(
            "4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1",
            "e1f1",
            cp_best=400,
            cp_played=0,
            best_pv=("e4d5",),
        )
    )
    assert result.category is MistakeCategory.MISSED_TACTIC
    assert result.evidence["material_delta_cp"] >= 200


def test_fr11_rule5_motif_is_hanging_capture_for_a_free_piece() -> None:
    result = classify(
        _context(
            "4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1",
            "e1f1",
            cp_best=400,
            cp_played=0,
            best_pv=("e4d5",),
        )
    )
    assert result.motif is Motif.HANGING_CAPTURE


def test_fr11_rule6_allowed_tactic_detects_the_reply_geometry() -> None:
    # Black's reply Nd2 forks the rooks on b1 and f1.
    result = classify(
        _context(
            "4k3/8/8/8/4n3/8/8/1R2KR2 w - - 0 1",
            "e1e2",
            cp_best=0,
            cp_played=-250,
            best_pv=("b1c1",),
            reply_pv=("e4d2",),
        )
    )
    assert result.category is MistakeCategory.ALLOWED_TACTIC
    assert result.motif is Motif.FORK


def test_fr11_rule7_endgame_technique_is_the_endgame_fallback() -> None:
    result = classify(
        _context(
            "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
            "e1d1",
            phase=Phase.ENDGAME,
            cp_best=0,
            cp_played=-150,
            w_before=0.80,
            w_after=0.50,
        )
    )
    assert result.category is MistakeCategory.ENDGAME_TECHNIQUE
    assert result.evidence["note"] == "spoiled_win"


def test_fr11_rule7_marks_a_spoiled_draw() -> None:
    result = classify(
        _context(
            "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
            "e1d1",
            phase=Phase.ENDGAME,
            w_before=0.50,
            w_after=0.20,
        )
    )
    assert result.evidence["note"] == "spoiled_draw"


def test_fr11_rule8_opening_principle_flags_a_repeated_piece_move() -> None:
    prior = ["g1f3", "g8f6", "f3e5", "f6e4"]
    board = chess.Board()
    for uci in prior:
        board.push(chess.Move.from_uci(uci))
    result = classify(
        MoveContext(
            board_before=board,
            move=chess.Move.from_uci("e5f3"),
            player_color=chess.WHITE,
            phase=Phase.OPENING,
            cp_best=0,
            cp_played=-150,
            w_before=0.5,
            w_after=0.35,
            best_uci=None,
            best_pv=(),
            reply_pv=(),
            prior_moves=tuple(prior),
        )
    )
    assert result.category is MistakeCategory.OPENING_PRINCIPLE
    assert result.evidence["violation"] == "repeated_piece_move"


def test_fr11_rule8_flags_an_early_queen() -> None:
    prior = ["e2e4", "e7e5"]
    board = chess.Board()
    for uci in prior:
        board.push(chess.Move.from_uci(uci))
    result = classify(
        MoveContext(
            board_before=board,
            move=chess.Move.from_uci("d1h5"),
            player_color=chess.WHITE,
            phase=Phase.OPENING,
            cp_best=0,
            cp_played=-150,
            w_before=0.5,
            w_after=0.35,
            best_uci=None,
            best_pv=(),
            reply_pv=(),
            prior_moves=tuple(prior),
        )
    )
    assert result.category is MistakeCategory.OPENING_PRINCIPLE
    assert result.evidence["violation"] == "early_queen"


def test_fr11_rule9_positional_drift_is_the_fallback() -> None:
    result = classify(
        _context(
            "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 1",
            "a2a3",
            cp_best=20,
            cp_played=-120,
            best_pv=("e1g1",),
            reply_pv=("e8g8",),
        )
    )
    assert result.category is MistakeCategory.POSITIONAL_DRIFT
    assert result.motif is Motif.NONE


def test_fr11_only_tactics_carry_motifs() -> None:
    result = classify(
        _context("7k/6pp/8/8/8/8/8/R6K w - - 0 1", "a1a2", cp_best=MATE_SCORE - 1, cp_played=0)
    )
    assert result.category is MistakeCategory.MISSED_MATE
    assert result.motif is Motif.NONE


def test_fr11_classification_is_deterministic() -> None:
    context = _context(
        "4k3/8/8/8/1p6/8/8/1N2K3 w - - 0 1", "b1c3", cp_played=-320, reply_pv=("b4c3",)
    )
    assert classify(context) == classify(context)
