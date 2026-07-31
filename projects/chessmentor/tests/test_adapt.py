"""FR-8 — the adaptive level controller: cold start, band, hysteresis, clamping."""

from __future__ import annotations

import pytest
from chessmentor.constants import LEVEL_HYSTERESIS, MODE_TARGETS, R_INIT
from chessmentor.engine.adapt import (
    cold_start_level_id,
    expected_score,
    ideal_opponent_elo,
    max_step_for,
    nearest_level_id,
    recommend_level_id,
    target_score,
)
from chessmentor.models import ChallengeMode


def test_fr8_mode_targets_are_the_documented_values() -> None:
    assert target_score(ChallengeMode.COMFORT) == 0.60
    assert target_score(ChallengeMode.BALANCED) == 0.50
    assert target_score(ChallengeMode.STRETCH) == 0.42
    assert set(MODE_TARGETS) == {"comfort", "balanced", "stretch"}


def test_fr8_ideal_opponent_offsets_match_the_spec() -> None:
    assert ideal_opponent_elo(1_000.0, ChallengeMode.BALANCED) == pytest.approx(1_000.0)
    assert ideal_opponent_elo(1_000.0, ChallengeMode.COMFORT) == pytest.approx(
        1_000.0 - 70.4, abs=0.2
    )
    assert ideal_opponent_elo(1_000.0, ChallengeMode.STRETCH) == pytest.approx(
        1_000.0 + 56.0, abs=0.2
    )


def test_fr8_ideal_opponent_hits_the_target_expected_score() -> None:
    for mode in ChallengeMode:
        elo_star = ideal_opponent_elo(1_234.0, mode)
        assert expected_score(1_234.0, elo_star) == pytest.approx(target_score(mode), abs=1e-9)


def test_fr8_cold_start_is_fully_determined(levels) -> None:
    for mode in ChallengeMode:
        expected = nearest_level_id(levels, ideal_opponent_elo(R_INIT, mode))
        assert cold_start_level_id(levels, mode) == expected
    # With the committed ladder these are concrete values.
    assert cold_start_level_id(levels, ChallengeMode.BALANCED) == 4
    assert cold_start_level_id(levels, ChallengeMode.COMFORT) == 3
    assert cold_start_level_id(levels, ChallengeMode.STRETCH) == 4


def test_fr8_nearest_level_breaks_ties_toward_the_lower_id(levels) -> None:
    midpoint = (levels[3].elo_internal + levels[4].elo_internal) / 2
    assert nearest_level_id(levels, midpoint) == levels[3].id


def test_fr8_hysteresis_keeps_the_current_level(levels) -> None:
    current = levels[3]
    inside_band = current.elo_internal  # E == 0.50 exactly
    assert (
        recommend_level_id(
            levels,
            current_level_id=current.id,
            r_hat=inside_band,
            mode=ChallengeMode.BALANCED,
            rated_games=10,
        )
        == current.id
    )


def test_fr8_hysteresis_band_is_plus_minus_five_points(levels) -> None:
    current = levels[4]
    # An expected score just inside the band keeps the level…
    just_inside = current.elo_internal + 400 * 0.0  # E = 0.5
    assert (
        recommend_level_id(
            levels,
            current_level_id=current.id,
            r_hat=just_inside,
            mode=ChallengeMode.BALANCED,
            rated_games=10,
        )
        == current.id
    )
    # …and clearly outside it moves.
    far_above = current.elo_internal + 400.0
    assert expected_score(far_above, current.elo_internal) - 0.5 > LEVEL_HYSTERESIS
    assert (
        recommend_level_id(
            levels,
            current_level_id=current.id,
            r_hat=far_above,
            mode=ChallengeMode.BALANCED,
            rated_games=10,
        )
        > current.id
    )


def test_fr8_moves_at_most_one_step_after_placement(levels) -> None:
    assert max_step_for(4) == 1
    assert (
        recommend_level_id(
            levels,
            current_level_id=1,
            r_hat=2_000.0,
            mode=ChallengeMode.BALANCED,
            rated_games=10,
        )
        == 2
    )


def test_fr8_moves_at_most_two_steps_during_placement(levels) -> None:
    assert max_step_for(0) == 2
    assert max_step_for(3) == 2
    assert (
        recommend_level_id(
            levels,
            current_level_id=1,
            r_hat=2_000.0,
            mode=ChallengeMode.BALANCED,
            rated_games=0,
        )
        == 3
    )


def test_fr8_clamps_outside_ladder(levels) -> None:
    """Named in EVALS.md: players below L1 and above L10 pin to the ladder ends."""
    lowest, highest = levels[0].id, levels[-1].id
    below = recommend_level_id(
        levels,
        current_level_id=lowest,
        r_hat=100.0,
        mode=ChallengeMode.BALANCED,
        rated_games=10,
    )
    assert below == lowest
    above = recommend_level_id(
        levels,
        current_level_id=highest,
        r_hat=3_000.0,
        mode=ChallengeMode.BALANCED,
        rated_games=10,
    )
    assert above == highest


def test_fr8_comfort_targets_a_weaker_opponent_than_stretch(levels) -> None:
    r_hat = 1_150.0
    comfort = recommend_level_id(
        levels,
        current_level_id=6,
        r_hat=r_hat,
        mode=ChallengeMode.COMFORT,
        rated_games=10,
    )
    stretch = recommend_level_id(
        levels,
        current_level_id=6,
        r_hat=r_hat,
        mode=ChallengeMode.STRETCH,
        rated_games=10,
    )
    assert comfort <= stretch


def test_fr8_every_rating_has_a_level_within_85_elo(levels) -> None:
    """FR-5's <=170 gap cap is what makes M3's perfect-controller ceiling 1.0."""
    low = levels[0].elo_internal + 80
    high = levels[-1].elo_internal - 80
    step = 5.0
    value = low
    while value <= high:
        for mode in ChallengeMode:
            elo_star = ideal_opponent_elo(value, mode)
            nearest = nearest_level_id(levels, elo_star)
            elo = next(lv.elo_internal for lv in levels if lv.id == nearest)
            assert abs(elo - elo_star) <= 85.0 + 1e-9, (value, mode)
        value += step


def test_fr8_repeated_recommendation_converges(levels) -> None:
    """The controller walks to the right rung and then stops moving."""
    current = cold_start_level_id(levels, ChallengeMode.BALANCED)
    r_hat = 1_600.0
    seen = []
    for game in range(20):
        current = recommend_level_id(
            levels,
            current_level_id=current,
            r_hat=r_hat,
            mode=ChallengeMode.BALANCED,
            rated_games=game,
        )
        seen.append(current)
    assert seen[-1] == seen[-2] == nearest_level_id(levels, r_hat)


def test_fr8_unknown_level_is_rejected(levels) -> None:
    with pytest.raises(KeyError):
        recommend_level_id(
            levels,
            current_level_id=99,
            r_hat=800.0,
            mode=ChallengeMode.BALANCED,
            rated_games=1,
        )
