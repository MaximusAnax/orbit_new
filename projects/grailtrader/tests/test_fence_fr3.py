"""FR-3: the MAD outlier fence."""

from __future__ import annotations

import math

import pytest
from grailtrader.engine.fence import MAD_TO_SIGMA, apply_fence, fence_cutoff

FLOOR = 0.7885
SIGMA = 3.5


def fence(prices):
    return apply_fence(prices, fence_sigma=SIGMA, fence_floor_log=FLOOR)


def test_fr3_floor_binds_when_the_stratum_is_quiet():
    prices = [1000.0 * math.exp(0.02 * (i - 10)) for i in range(21)]
    result = fence(prices)
    assert result.sigma_hat * SIGMA < FLOOR
    assert result.cutoff == pytest.approx(FLOOR)
    assert result.n_excluded == 0


def test_fr3_mad_term_binds_on_a_genuinely_volatile_stratum():
    prices = [1000.0 * math.exp(0.6 * (i - 10)) for i in range(21)]
    result = fence(prices)
    assert SIGMA * result.sigma_hat > FLOOR
    assert result.cutoff == pytest.approx(SIGMA * result.sigma_hat)


def test_fr3_excludes_counterfeit_priced_dumps_and_mislabelled_grails():
    clean = [1000.0] * 20
    prices = [*clean, 200.0, 4000.0]  # |ln| = 1.61 and 1.39, both beyond the 0.7885 floor
    result = fence(prices)
    assert set(result.excluded) == {20, 21}
    assert result.n_kept == 20


def test_fr3_keeps_a_fake_priced_near_market_by_construction():
    """SCOPE non-goal 3: a price-only rule cannot separate a 0.5x fake from a snipe."""
    prices = [*[1000.0] * 20, 500.0]  # |ln| = 0.693 < 0.7885
    result = fence(prices)
    assert result.excluded == ()


def test_fr3_cutoff_is_the_max_of_the_mad_term_and_the_floor():
    assert fence_cutoff(0.1, fence_sigma=SIGMA, fence_floor_log=FLOOR) == pytest.approx(FLOOR)
    assert fence_cutoff(0.5, fence_sigma=SIGMA, fence_floor_log=FLOOR) == pytest.approx(1.75)


def test_fr3_sigma_hat_uses_the_mad_consistency_constant():
    prices = [math.exp(x) for x in (-1.0, 0.0, 0.0, 0.0, 1.0)]
    result = fence(prices)
    assert result.median_log == pytest.approx(0.0)
    assert result.sigma_hat == pytest.approx(MAD_TO_SIGMA * 0.0)


def test_fr3_boundary_is_strict_inequality():
    edge = math.exp(FLOOR)
    prices = [*[1.0] * 10, edge]
    assert fence(prices).excluded == ()
    prices = [*[1.0] * 10, edge * 1.0001]
    assert fence(prices).excluded == (10,)


def test_fr3_empty_window_is_handled():
    result = fence([])
    assert result.n_kept == 0 and result.n_excluded == 0


def test_fr3_rejects_non_positive_prices():
    with pytest.raises(ValueError, match="strictly positive"):
        fence([1.0, 0.0])
