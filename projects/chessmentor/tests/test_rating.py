"""FR-7 — rating estimation: Glicko-1, RD inflation, ACPL→perf, precision blend."""

from __future__ import annotations

import math
from itertools import pairwise

import pytest
from chessmentor.constants import (
    DIVERGENCE_STREAK,
    OPP_RD,
    PERF_CLAMP,
    PERF_SIGMA,
    PERF_SIGMA_1,
    R_INIT,
    RD_FLOOR,
    RD_INFLATE_TO,
    RD_INIT,
    SURPRISE_WINDOW,
)
from chessmentor.engine.rating import (
    apply_rated_game,
    blend_lambda,
    blended_rating,
    expected_score,
    glicko_update,
    initial_rating_state,
    perf_rating_from_acpl,
)
from chessmentor.models import ChallengeMode, RatingState

NOW = "2026-07-31T12:00:00Z"


def _state(**overrides: object) -> RatingState:
    base = initial_rating_state(current_level_id=4, updated_at=NOW)
    return base.model_copy(update=overrides)


# --- (a) results channel ---------------------------------------------------- #


def test_fr7a_expected_score_is_the_plain_elo_curve() -> None:
    assert expected_score(800.0, 800.0) == pytest.approx(0.5)
    assert expected_score(1200.0, 800.0) == pytest.approx(1 / (1 + 10 ** (-1)), rel=1e-9)
    assert expected_score(800.0, 1200.0) == pytest.approx(1 / (1 + 10**1), rel=1e-9)


def test_fr7a_glicko_win_raises_rating_and_loss_lowers_it() -> None:
    won, rd_won = glicko_update(800, 200, opponent_elo=800, opponent_rd=OPP_RD, score=1.0)
    lost, rd_lost = glicko_update(800, 200, opponent_elo=800, opponent_rd=OPP_RD, score=0.0)
    drawn, _ = glicko_update(800, 200, opponent_elo=800, opponent_rd=OPP_RD, score=0.5)
    assert won > 800 > lost
    assert drawn == pytest.approx(800, abs=1e-6)
    assert rd_won == rd_lost < 200


def test_fr7a_rd_shrinks_with_evidence_and_never_passes_the_floor() -> None:
    rating, rd = R_INIT, RD_INIT
    for _ in range(200):
        rating, rd = glicko_update(rating, rd, opponent_elo=800, opponent_rd=OPP_RD, score=0.5)
    assert rd == pytest.approx(RD_FLOOR)


def test_fr7a_update_magnitude_matches_the_documented_arithmetic() -> None:
    """EVALS M2b rationale: ~20*(s-E) Elo at the floor, ~109*(s-E) at RD 150.

    Here ``s - E = 0.5``, so the two coefficients show up halved.
    """
    at_floor, _ = glicko_update(800, RD_FLOOR, opponent_elo=800, opponent_rd=OPP_RD, score=1.0)
    at_inflated, _ = glicko_update(
        800, RD_INFLATE_TO, opponent_elo=800, opponent_rd=OPP_RD, score=1.0
    )
    assert (at_floor - 800) / 0.5 == pytest.approx(20.1, abs=1.0)
    assert (at_inflated - 800) / 0.5 == pytest.approx(109.3, abs=2.0)


def test_fr7a_surprise_window_holds_only_the_last_n_games(levels) -> None:
    state = _state()
    for game_id in range(1, 8):
        outcome = apply_rated_game(
            state,
            game_id=game_id,
            result_score=1.0,
            opponent_elo=1_600.0,
            level_played=9,
            perf_game=1_600.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        state = outcome.state
        assert len(state.surprise_window) <= SURPRISE_WINDOW


def test_fr7a_regime_change_inflates_rd(levels) -> None:
    """US-4 / M2b: a run of surprising results must re-inflate RD."""
    state = _state()
    # Settle first: many expected results against a matched opponent.
    for game_id in range(1, 25):
        state = apply_rated_game(
            state,
            game_id=game_id,
            result_score=0.5,
            opponent_elo=state.glicko_rating,
            level_played=4,
            perf_game=state.glicko_rating,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        ).state
    settled_rd = state.glicko_rd
    assert settled_rd < RD_INFLATE_TO

    inflated = False
    for game_id in range(25, 33):
        outcome = apply_rated_game(
            state,
            game_id=game_id,
            result_score=1.0,
            opponent_elo=state.glicko_rating + 300.0,
            level_played=9,
            perf_game=state.glicko_rating + 300.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        state = outcome.state
        inflated = inflated or outcome.event.rd_inflated
    assert inflated
    assert state.glicko_rd > settled_rd


def test_fr7a_inflation_cannot_refire_immediately(levels) -> None:
    state = _state()
    fired_at: list[int] = []
    for game_id in range(1, 21):
        outcome = apply_rated_game(
            state,
            game_id=game_id,
            result_score=1.0,
            opponent_elo=2_000.0,
            level_played=10,
            perf_game=1_800.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        state = outcome.state
        if outcome.event.rd_inflated:
            fired_at.append(game_id)
    for earlier, later in pairwise(fired_at):
        assert later - earlier >= SURPRISE_WINDOW


def test_fr7a_a_biased_move_quality_channel_never_inflates_rd(levels) -> None:
    """M2c's premise: inflation keys on the *results* channel alone."""
    state = _state()
    for game_id in range(1, 21):
        outcome = apply_rated_game(
            state,
            game_id=game_id,
            # Results are exactly as expected, so there is no surprise…
            result_score=0.5,
            opponent_elo=state.glicko_rating,
            level_played=4,
            # …while the move-quality channel is 250 Elo low every single game.
            perf_game=state.glicko_rating - 250.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        state = outcome.state
        assert not outcome.event.rd_inflated


# --- (b) move-quality channel ------------------------------------------------ #


def test_fr7b_perf_interpolation_hits_the_anchors(levels) -> None:
    for level in levels:
        assert perf_rating_from_acpl(level.acpl_mean, levels) == pytest.approx(
            level.elo_internal, abs=1e-6
        )


def test_fr7b_perf_is_monotone_decreasing_in_acpl(levels) -> None:
    values = [perf_rating_from_acpl(acpl, levels) for acpl in range(40, 260, 5)]
    for earlier, later in pairwise(values):
        assert later <= earlier


def test_fr7b_perf_is_clamped(levels) -> None:
    low, high = PERF_CLAMP
    assert perf_rating_from_acpl(0.0, levels) <= high
    assert perf_rating_from_acpl(5_000.0, levels) >= low


def test_fr7b_perf_interpolates_between_anchors(levels) -> None:
    first, second = levels[3], levels[4]
    midpoint = (first.acpl_mean + second.acpl_mean) / 2
    value = perf_rating_from_acpl(midpoint, levels)
    assert min(first.elo_internal, second.elo_internal) < value
    assert value < max(first.elo_internal, second.elo_internal)


def test_fr7b_ewma_initializes_then_smooths(levels) -> None:
    state = _state()
    first = apply_rated_game(
        state,
        game_id=1,
        result_score=0.5,
        opponent_elo=850.0,
        level_played=4,
        perf_game=1_000.0,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    )
    assert first.event.perf_ewma_after == pytest.approx(1_000.0)
    second = apply_rated_game(
        first.state,
        game_id=2,
        result_score=0.5,
        opponent_elo=850.0,
        level_played=4,
        perf_game=600.0,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    )
    assert second.event.perf_ewma_after == pytest.approx(0.35 * 600.0 + 0.65 * 1_000.0)


# --- (c) precision-weighted blend -------------------------------------------- #


def test_fr7c_lambda_is_inverse_variance_weighting() -> None:
    assert blend_lambda(RD_INIT, 5) == pytest.approx(PERF_SIGMA**2 / (PERF_SIGMA**2 + RD_INIT**2))
    assert blend_lambda(RD_INIT, 1) == pytest.approx(
        PERF_SIGMA_1**2 / (PERF_SIGMA_1**2 + RD_INIT**2)
    )
    assert blend_lambda(RD_FLOOR, 5) == pytest.approx(0.6923, abs=1e-3)
    assert blend_lambda(143.0, 5) == pytest.approx(0.284, abs=1e-3)


def test_fr7c_lambda_grows_as_rd_shrinks() -> None:
    values = [blend_lambda(rd, 5) for rd in (350, 250, 150, 100, 60)]
    for earlier, later in pairwise(values):
        assert later > earlier


def test_fr7c_lambda_does_not_depend_on_game_count() -> None:
    assert blend_lambda(120.0, 2) == blend_lambda(120.0, 40)


def test_fr7c_cold_start_is_move_quality_dominated(levels) -> None:
    """US-2: with RD 350 the fast channel carries the estimate."""
    state = _state()
    outcome = apply_rated_game(
        state,
        game_id=1,
        result_score=0.5,
        opponent_elo=850.0,
        level_played=4,
        perf_game=1_400.0,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    )
    assert outcome.event.lambda_used < 0.30
    assert outcome.event.r_hat_after > 1_200.0


def test_fr7c_r_hat_falls_back_to_glicko_before_any_judged_game() -> None:
    state = _state()
    assert state.perf_ewma is None
    assert blended_rating(state) == pytest.approx(R_INIT)


def test_fr7c_replaying_events_reproduces_the_blend(levels) -> None:
    state = _state()
    for game_id in range(1, 11):
        outcome = apply_rated_game(
            state,
            game_id=game_id,
            result_score=1.0 if game_id % 2 else 0.0,
            opponent_elo=900.0,
            level_played=4,
            perf_game=950.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        assert outcome.event.r_hat_after == pytest.approx(blended_rating(outcome.state))
        state = outcome.state


# --- (d) divergence warning --------------------------------------------------- #


def test_fr7d_divergence_warning_needs_a_streak(levels) -> None:
    state = _state()
    for game_id in range(1, DIVERGENCE_STREAK + 1):
        outcome = apply_rated_game(
            state,
            game_id=game_id,
            result_score=0.5,
            opponent_elo=800.0,
            level_played=4,
            perf_game=1_800.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        state = outcome.state
        assert state.divergence_streak == game_id
        assert state.calibration_warning == (game_id >= DIVERGENCE_STREAK)


def test_fr7d_divergence_streak_resets_on_agreement(levels) -> None:
    state = _state()
    state = apply_rated_game(
        state,
        game_id=1,
        result_score=0.5,
        opponent_elo=800.0,
        level_played=4,
        perf_game=1_800.0,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    ).state
    assert state.divergence_streak == 1
    # The EWMA has to walk back before the channels agree again.
    for game_id in range(2, 9):
        state = apply_rated_game(
            state,
            game_id=game_id,
            result_score=0.5,
            opponent_elo=state.glicko_rating,
            level_played=4,
            perf_game=state.glicko_rating,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        ).state
    assert state.divergence_streak == 0
    assert not state.calibration_warning


def test_fr7d_warning_changes_no_math(levels) -> None:
    state = _state()
    warned = state
    for game_id in range(1, 7):
        warned = apply_rated_game(
            warned,
            game_id=game_id,
            result_score=0.5,
            opponent_elo=800.0,
            level_played=4,
            perf_game=1_800.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        ).state
    assert warned.calibration_warning
    # The blend still follows the same formula.
    assert blended_rating(warned) == pytest.approx(
        blend_lambda(warned.glicko_rd, warned.judged_games) * warned.glicko_rating
        + (1 - blend_lambda(warned.glicko_rd, warned.judged_games)) * (warned.perf_ewma or 0.0)
    )


# --- ordering and event bookkeeping ------------------------------------------- #


def test_fr7_events_must_be_applied_in_termination_order(levels) -> None:
    state = _state()
    state = apply_rated_game(
        state,
        game_id=5,
        result_score=1.0,
        opponent_elo=850.0,
        level_played=4,
        perf_game=900.0,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    ).state
    with pytest.raises(ValueError, match="termination order"):
        apply_rated_game(
            state,
            game_id=4,
            result_score=1.0,
            opponent_elo=850.0,
            level_played=4,
            perf_game=900.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )


def test_fr7_rejects_an_impossible_result(levels) -> None:
    with pytest.raises(ValueError, match="result_score"):
        apply_rated_game(
            _state(),
            game_id=1,
            result_score=0.75,
            opponent_elo=850.0,
            level_played=4,
            perf_game=900.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )


def test_fr7_event_records_every_component(levels) -> None:
    outcome = apply_rated_game(
        _state(),
        game_id=1,
        result_score=0.0,
        opponent_elo=850.0,
        level_played=4,
        perf_game=845.0,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    )
    event = outcome.event
    assert event.glicko_r_before == R_INIT
    assert event.glicko_rd_before == RD_INIT
    assert event.glicko_r_after < event.glicko_r_before
    assert event.glicko_rd_after < event.glicko_rd_before
    assert event.expected_score == pytest.approx(expected_score(R_INIT, 850.0))
    assert event.surprise_after == pytest.approx(0.0 - event.expected_score)
    assert event.perf_game == pytest.approx(845.0)
    assert event.lambda_used == pytest.approx(blend_lambda(event.glicko_rd_after, 1))
    assert event.level_played == 4
    assert event.level_next == outcome.state.current_level_id


def test_fr7_all_book_game_skips_the_move_quality_channel(levels) -> None:
    """The degenerate case: no non-book player move exists to measure."""
    state = _state()
    outcome = apply_rated_game(
        state,
        game_id=1,
        result_score=1.0,
        opponent_elo=850.0,
        level_played=4,
        perf_game=None,
        levels=levels,
        mode=ChallengeMode.BALANCED,
        now=NOW,
    )
    assert outcome.state.judged_games == 0
    assert outcome.state.perf_ewma is None
    assert outcome.event.lambda_used == 1.0
    assert outcome.event.r_hat_after == pytest.approx(outcome.event.glicko_r_after)


def test_fr7_rd_never_leaves_its_bounds(levels) -> None:
    state = _state()
    for game_id in range(1, 40):
        state = apply_rated_game(
            state,
            game_id=game_id,
            result_score=float(game_id % 2),
            opponent_elo=850.0,
            level_played=4,
            perf_game=900.0,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        ).state
        assert RD_FLOOR <= state.glicko_rd <= RD_INIT
        assert not math.isnan(state.glicko_rating)
