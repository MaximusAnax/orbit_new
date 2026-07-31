"""FR-9 — post-game judgment: win model, severity, book exclusion, aggregates."""

from __future__ import annotations

from itertools import pairwise

import pytest
from chessmentor.constants import (
    CP_LOSS_CAP,
    MATE_SCORE,
    SEV_BLUNDER,
    SEV_INACCURACY,
    SEV_MISTAKE,
    WIN_K,
)
from chessmentor.engine.judge import (
    included_move_count,
    judge_game,
    move_accuracy,
    win_probability,
)
from chessmentor.models import AnalystKind, Color, Phase, Severity, severity_for

#: A cheap analyst configuration; JUDGE_BUDGET belongs to the eval suite, which
#: is where analyst strength is actually gated (M7).
TEST_ANALYST_BUDGET = 700

ITALIAN_GAME = [
    "e2e4",
    "e7e5",
    "g1f3",
    "b8c6",
    "f1c4",
    "g8f6",
    "f3g5",
    "d7d5",
    "e4d5",
    "c6a5",
    "c4b5",
    "c7c6",
    "d5c6",
    "b7c6",
    "b5e2",
    "h7h6",
]


# --- the win-probability model ------------------------------------------------ #


def test_fr9_win_probability_is_the_lichess_curve_on_zero_to_one() -> None:
    assert win_probability(0) == pytest.approx(0.5)
    assert 0.0 <= win_probability(-5_000) < 0.01
    assert 0.99 < win_probability(5_000) <= 1.0
    assert win_probability(100) == pytest.approx(1 / (1 + pow(2.718281828459045, -WIN_K * 100)))


def test_fr9_win_probability_is_monotone_and_symmetric() -> None:
    for cp in (0, 50, 150, 400, 900):
        assert win_probability(cp) + win_probability(-cp) == pytest.approx(1.0)
    values = [win_probability(cp) for cp in range(-800, 801, 50)]
    for earlier, later in pairwise(values):
        assert later > earlier


def test_fr9_mate_scores_saturate_the_win_model() -> None:
    assert win_probability(MATE_SCORE - 1) == 1.0
    assert win_probability(-(MATE_SCORE - 1)) == 0.0


# --- severity tiers ------------------------------------------------------------ #


def test_fr9_severity_thresholds_are_the_corrected_lichess_drops() -> None:
    assert severity_for(0.0) is Severity.OK
    assert severity_for(SEV_INACCURACY - 1e-9) is Severity.OK
    assert severity_for(SEV_INACCURACY) is Severity.INACCURACY
    assert severity_for(SEV_MISTAKE) is Severity.MISTAKE
    assert severity_for(SEV_BLUNDER) is Severity.BLUNDER
    assert severity_for(0.9) is Severity.BLUNDER


def test_fr9_hanging_a_knight_from_equality_is_a_blunder() -> None:
    """D1's regression case: on the old (halved) scale this scored a *mistake*."""
    delta_w = win_probability(0) - win_probability(-320)
    assert delta_w > SEV_BLUNDER
    assert severity_for(delta_w) is Severity.BLUNDER


def test_fr9_a_decided_position_is_not_re_flagged() -> None:
    """M4's decided-position trap: 300 cp lost at +800 is not a blunder."""
    delta_w = win_probability(800) - win_probability(500)
    assert delta_w < SEV_MISTAKE
    assert severity_for(delta_w) in (Severity.OK, Severity.INACCURACY)


# --- accuracy ------------------------------------------------------------------- #


def test_fr9_accuracy_is_100_for_a_perfect_move_and_decays() -> None:
    # The published curve peaks at 103.1668 - 3.1669 = 99.9999, not exactly 100.
    assert move_accuracy(0.0) == pytest.approx(100.0, abs=0.001)
    assert move_accuracy(0.05) < 100.0
    assert move_accuracy(0.30) < move_accuracy(0.10)
    assert 0.0 <= move_accuracy(1.0) <= 100.0


def test_fr9_accuracy_is_clamped_to_the_unit_percentage_range() -> None:
    for delta_w in (0.0, 0.01, 0.5, 1.0, 5.0):
        assert 0.0 <= move_accuracy(delta_w) <= 100.0


# --- the judge pass -------------------------------------------------------------- #


@pytest.fixture(scope="module")
def judged(levels):
    from chessmentor.adapters import InternalAnalyst

    return judge_game(
        uci_moves=ITALIAN_GAME,
        player_color=Color.WHITE,
        book_depth=6,
        analyst=InternalAnalyst(max_depth=3),
        levels=levels,
        created_at="2026-07-31T12:00:00Z",
        node_budget=TEST_ANALYST_BUDGET,
        is_rating_basis=True,
    )


def test_fr9_judge_analyses_only_the_player_s_moves(judged) -> None:
    assert [m.ply for m in judged.moves] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_fr9_book_plies_are_stored_but_excluded(judged) -> None:
    excluded = [m for m in judged.moves if not m.in_acpl]
    assert [m.ply for m in excluded] == [1, 3, 5]
    assert all(m.ply <= judged.book_depth for m in excluded)
    assert included_move_count(judged) == 5


def test_fr9_acpl_uses_only_included_moves(judged) -> None:
    included = [m for m in judged.moves if m.in_acpl]
    expected = sum(m.cp_loss for m in included) / len(included)
    assert judged.acpl == pytest.approx(expected)


def test_fr9_accuracy_is_the_plain_mean_over_included_moves(judged) -> None:
    included = [m for m in judged.moves if m.in_acpl]
    expected = sum(move_accuracy(m.delta_w) for m in included) / len(included)
    assert judged.accuracy == pytest.approx(expected)


def test_fr9_cp_loss_is_capped_and_non_negative(judged) -> None:
    for move in judged.moves:
        assert 0 <= move.cp_loss <= CP_LOSS_CAP
        assert move.cp_loss == min(CP_LOSS_CAP, max(0, move.cp_best - move.cp_played))


def test_fr9_every_move_stores_a_best_move_and_a_line(judged) -> None:
    for move in judged.moves:
        assert len(move.best_uci) >= 4
        assert move.best_line_san
        assert len(move.best_line_san) <= 6


def test_fr9_key_moments_are_the_top_three_included_swings(judged) -> None:
    included = sorted((m for m in judged.moves if m.in_acpl), key=lambda m: (-m.delta_w, m.ply))
    assert [k.ply for k in judged.key_moments] == [m.ply for m in included[:3]]
    assert all(k.dw >= 0 for k in judged.key_moments)


def test_fr9_per_phase_aggregates_cover_only_included_moves(judged) -> None:
    for phase, stats in judged.per_phase.items():
        moves = [m for m in judged.moves if m.in_acpl and m.phase is phase]
        assert stats.n_moves == len(moves)
        assert stats.dw_sum == pytest.approx(sum(m.delta_w for m in moves))
    assert Phase.OPENING not in judged.per_phase or judged.per_phase[Phase.OPENING].n_moves > 0


def test_fr9_analysis_records_its_configuration(judged) -> None:
    assert judged.analyst is AnalystKind.INTERNAL
    assert judged.analyst_version
    assert judged.node_budget == TEST_ANALYST_BUDGET
    assert judged.is_rating_basis


def test_fr9_severity_counts_match_the_move_rows(judged) -> None:
    included = [m for m in judged.moves if m.in_acpl]
    assert judged.n_blunders == sum(1 for m in included if m.severity is Severity.BLUNDER)
    assert judged.n_mistakes == sum(1 for m in included if m.severity is Severity.MISTAKE)
    assert judged.n_inaccuracies == sum(1 for m in included if m.severity is Severity.INACCURACY)


def test_fr9_only_flagged_included_moves_carry_a_category(judged) -> None:
    for move in judged.moves:
        should = move.in_acpl and move.severity in (Severity.MISTAKE, Severity.BLUNDER)
        assert (move.category is not None) == should


def test_fr16_re_running_the_judge_is_byte_identical(levels) -> None:
    from chessmentor.adapters import InternalAnalyst

    def run():
        return judge_game(
            uci_moves=ITALIAN_GAME[:10],
            player_color=Color.WHITE,
            book_depth=4,
            analyst=InternalAnalyst(max_depth=3),
            levels=levels,
            created_at="2026-07-31T12:00:00Z",
            node_budget=TEST_ANALYST_BUDGET,
        )

    assert run().model_dump_json() == run().model_dump_json()


def test_fr9_black_perspective_is_not_sign_flipped(levels) -> None:
    """M10's wiring check in miniature: the same game judged from both sides."""
    from chessmentor.adapters import InternalAnalyst

    analyst = InternalAnalyst(max_depth=3)
    white = judge_game(
        uci_moves=ITALIAN_GAME[:8],
        player_color=Color.WHITE,
        book_depth=0,
        analyst=analyst,
        levels=levels,
        created_at="t",
        node_budget=TEST_ANALYST_BUDGET,
    )
    black = judge_game(
        uci_moves=ITALIAN_GAME[:8],
        player_color=Color.BLACK,
        book_depth=0,
        analyst=analyst,
        levels=levels,
        created_at="t",
        node_budget=TEST_ANALYST_BUDGET,
    )
    assert [m.ply for m in white.moves] == [1, 3, 5, 7]
    assert [m.ply for m in black.moves] == [2, 4, 6, 8]
    # Both are sane opening moves, so neither side should look catastrophic.
    assert white.acpl < CP_LOSS_CAP
    assert black.acpl < CP_LOSS_CAP


def test_fr9_rejects_an_illegal_move_list(levels) -> None:
    from chessmentor.adapters import InternalAnalyst

    with pytest.raises(ValueError, match="illegal move"):
        judge_game(
            uci_moves=["e2e4", "e7e5", "e2e4"],
            player_color=Color.WHITE,
            book_depth=0,
            analyst=InternalAnalyst(max_depth=2),
            levels=levels,
            created_at="t",
            node_budget=TEST_ANALYST_BUDGET,
        )


def test_fr9_all_book_game_has_no_included_moves(levels) -> None:
    from chessmentor.adapters import InternalAnalyst

    analysis = judge_game(
        uci_moves=ITALIAN_GAME[:6],
        player_color=Color.WHITE,
        book_depth=6,
        analyst=InternalAnalyst(max_depth=2),
        levels=levels,
        created_at="t",
        node_budget=TEST_ANALYST_BUDGET,
    )
    assert included_move_count(analysis) == 0
    assert analysis.acpl == 0.0
    assert analysis.key_moments == []
