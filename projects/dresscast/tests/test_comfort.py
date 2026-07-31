"""FR-5 feels-like and FR-6 requirement / achievable band / ``S_thermal``."""

from __future__ import annotations

import math

import pytest
from conftest import diurnal, make_forecast, params, small_wardrobe
from dresscast.engine.assemble import build_candidates
from dresscast.engine.comfort import (
    achievable_band,
    apparent_temperature,
    bare_feels_c,
    build_day_context,
    config_feels_c,
    effective_wind,
    ensemble_clo,
    feels_like,
    hour_score,
    ramp_cold,
    ramp_heat,
    required_clo,
    thermal_score,
    wind_chill,
    wind_delta,
)
from dresscast.engine.models import (
    COMFORT_BAND_CLO,
    EXPOSURE_COMMUTE,
    ICL_INTERCEPT,
    ICL_SLOPE,
    Band,
)
from dresscast.errors import InvalidParams

# --------------------------------------------------------------------------
# FR-5 — feels-like
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("temp_c", "wind_kmh", "chart_cell"),
    [
        # Environment Canada wind-chill chart, °C, integer cells.
        (-10.0, 20.0, -18.0),
        (0.0, 30.0, -6.0),
        (-5.0, 10.0, -9.0),
        (-20.0, 40.0, -34.0),
        (5.0, 20.0, 1.0),
        (-30.0, 50.0, -49.0),
    ],
)
def test_fr5_wind_chill_matches_environment_canada_chart(temp_c, wind_kmh, chart_cell):
    """The JAG/TI 2001 formula reproduces published chart cells within 0.5 °C."""
    assert abs(wind_chill(temp_c, wind_kmh) - chart_cell) <= 0.5


def test_fr5_wind_delta_is_zero_below_the_validity_wind():
    assert wind_delta(-5.0, 4.8) == 0.0
    assert wind_delta(-5.0, 4.0) == 0.0
    assert wind_delta(-5.0, 5.0) != 0.0


def test_fr5_steadman_apparent_temperature_worked_example():
    """30 °C at 70% RH and 10 km/h → 33.82 °C (Steadman 1984, BoM form)."""
    vapour = 0.7 * 6.105 * math.exp(17.27 * 30.0 / (237.7 + 30.0))
    expected = 30.0 + 0.33 * vapour - 0.70 * (10.0 / 3.6) - 4.0
    assert apparent_temperature(30.0, 70.0, 10.0) == pytest.approx(expected)
    assert apparent_temperature(30.0, 70.0, 10.0) == pytest.approx(33.824, abs=0.01)


def test_fr5_ramps_have_disjoint_support():
    for temp in (-20.0, 0.0, 9.9, 10.0, 12.0, 14.0, 20.0, 24.0, 25.0, 26.0, 40.0):
        assert ramp_cold(temp) == 0.0 or ramp_heat(temp) == 0.0


def test_fr5_ramp_endpoints():
    assert ramp_cold(10.0) == 1.0
    assert ramp_cold(14.0) == 0.0
    assert ramp_cold(12.0) == pytest.approx(0.5)
    assert ramp_heat(24.0) == 0.0
    assert ramp_heat(26.0) == 1.0
    assert ramp_heat(25.0) == pytest.approx(0.5)


def test_fr5_feels_like_is_lipschitz_3_across_both_ramp_edges():
    """Revision 1's switch gave a 4.4 °C jump for a 0.1 °C input change (D4)."""
    worst = 0.0
    temp = -10.0
    while temp <= 40.0:
        a = feels_like(temp, 45.0, 60.0)
        b = feels_like(temp + 0.05, 45.0, 60.0)
        worst = max(worst, abs(a - b) / 0.05)
        temp += 0.05
    assert worst <= 3.0


def test_fr5_no_wind_delta_above_the_cold_ramp_and_no_heat_delta_below_it():
    assert feels_like(14.0, 60.0, 50.0) == pytest.approx(14.0)
    assert feels_like(24.0, 5.0, 90.0) == pytest.approx(24.0)


def test_fr5_effective_wind_attenuation_by_windproofness():
    assert effective_wind(15.0, 0) == pytest.approx(15.0)
    assert effective_wind(15.0, 1) == pytest.approx(9.0)
    assert effective_wind(15.0, 2) == pytest.approx(4.5)


def test_fr5_bare_and_config_feels_split_matches_the_worked_example():
    """DATA_MODEL.md §6: 6 °C at 15 km/h → bare 2.982, windproof-1 4.070."""
    forecast = make_forecast(temps=6.0, wind=15.0, humidity=70.0)
    hour = forecast.hours[7]
    assert bare_feels_c(hour) == pytest.approx(2.982, abs=5e-4)
    assert config_feels_c(hour, 1) == pytest.approx(4.070, abs=5e-4)
    assert config_feels_c(hour, 0) == pytest.approx(bare_feels_c(hour))


def test_fr5_windproofness_2_drops_wind_below_the_chill_threshold():
    """15 km/h through a windproofness-2 shell is 4.5 km/h — no wind delta."""
    forecast = make_forecast(temps=2.0, wind=15.0, humidity=70.0)
    assert config_feels_c(forecast.hours[7], 2) == pytest.approx(2.0)


# --------------------------------------------------------------------------
# FR-6.1 — required insulation
# --------------------------------------------------------------------------


def test_fr6_required_clo_definitional_anchor():
    """(21 °C, met 1.0) is the definition of 1 clo; the model gives 0.997 (D3)."""
    assert required_clo(21.0, 1.0) == pytest.approx(0.997, abs=1e-3)


def test_fr6_required_clo_spec_anchor_22c_met_1_1():
    """(34-22)/(7.66·1.1) - 0.70 = 0.72416 — REVIEW.md finding 10."""
    assert required_clo(22.0, 1.1) == pytest.approx(0.724, abs=1e-3)


def test_fr6_required_clo_ashrae_zone_residuals_stay_within_030():
    """D3's residual table is a stated limitation; M1 fails if it grows."""
    assert abs(required_clo(21.75, 1.1) - 1.000) <= 0.30
    assert abs(required_clo(24.5, 1.1) - 0.500) <= 0.30


def test_fr6_required_clo_clamps_both_ends():
    assert required_clo(60.0, 1.6) == 0.0
    assert required_clo(-60.0, 1.0) == 4.5


def test_fr6_required_clo_scales_with_met():
    assert required_clo(5.0, 1.2) > required_clo(5.0, 1.6) > required_clo(5.0, 2.2)


def test_fr6_hour_score_is_flat_in_band_then_linear_to_zero():
    assert hour_score(0.0) == 1.0
    assert hour_score(-COMFORT_BAND_CLO) == 1.0
    assert hour_score(0.25) == 1.0
    assert hour_score(-0.287) == pytest.approx(0.9506666, abs=1e-6)
    assert hour_score(1.0) == 0.0
    assert hour_score(-2.0) == 0.0


# --------------------------------------------------------------------------
# FR-7 / D2 — ensemble insulation
# --------------------------------------------------------------------------


def test_fr7_ensemble_regression_matches_the_worked_example(wardrobe):
    """Σ 1.55 → Icl 1.455 and Σ 0.59 → Icl 0.654 (DATA_MODEL.md §6)."""
    by_id = {g.id: g for g in wardrobe}
    winter = [by_id[i] for i in ("b5-oxford", "m3-thick", "o3-coat", "p4-jeans", "f3-boots")]
    assert sum(g.clo for g in winter) == pytest.approx(1.55)
    assert ensemble_clo(winter) == pytest.approx(1.455, abs=5e-4)
    afternoon = [by_id[i] for i in ("b5-oxford", "p4-jeans", "f3-boots")]
    assert ensemble_clo(afternoon) == pytest.approx(0.654, abs=5e-4)


def test_fr7_ensemble_of_the_empty_set_is_zero():
    assert ensemble_clo([]) == 0.0


# --------------------------------------------------------------------------
# FR-6.2 / D17 — the achievable band
# --------------------------------------------------------------------------


def test_fr6_achievable_band_reproduces_the_evals_arithmetic(wardrobe):
    """EVALS.md §5.2: the small wardrobe reaches Icl ∈ [0.311, 2.040]."""
    request = params(occasion="casual")
    forecast = make_forecast(temps=diurnal(-6.0, -2.0), wind=5.0, humidity=70.0)
    ctx = build_day_context(forecast, request)
    band = achievable_band(ctx, build_candidates(wardrobe, request, ctx), occasion="casual")
    assert band.ceiling == pytest.approx(ICL_SLOPE * 2.25 + ICL_INTERCEPT, abs=1e-6)
    assert min(band.floor) == pytest.approx(ICL_SLOPE * 0.18 + ICL_INTERCEPT, abs=1e-6)


def test_fr6_band_floor_rises_on_hours_that_mandate_worn_cover(wardrobe):
    """A heavy-rain hour forces the cheapest adequate shell into the floor."""
    request = params(occasion="casual")
    prob = [0.05] * 24
    mmh = [0.0] * 24
    for hour in range(14, 18):
        prob[hour] = 0.7
        mmh[hour] = 12.0
    forecast = make_forecast(temps=diurnal(22.0, 30.0), precip_prob=prob, precip_mmh=mmh, wind=12.0)
    ctx = build_day_context(forecast, request)
    band = achievable_band(ctx, build_candidates(wardrobe, request, ctx), occasion="casual")
    dry = [hc.index for hc in ctx.hours if not hc.hard_rain]
    wet = [hc.index for hc in ctx.hours if hc.hard_rain]
    assert wet, "the fixture must contain hard-rain hours"
    assert band.floor[wet[0]] > band.floor[dry[0]]
    # tee .08 + shorts .08 + sandals .02 + the .25 rain shell
    assert band.floor[wet[0]] == pytest.approx(ICL_SLOPE * 0.43 + ICL_INTERCEPT, abs=1e-6)


def test_fr6_band_clamp_reports_the_side_it_clamped_to():
    band = Band(ceiling=2.0, floor=(0.5, 0.5))
    assert band.clamp(1.0, 0) == (1.0, None)
    assert band.clamp(0.1, 0) == (0.5, "wardrobe_floor")
    assert band.clamp(3.0, 1) == (2.0, "wardrobe_ceiling")


def test_fr7_leg_base_is_gated_on_bare_feels_like(wardrobe):
    """HC-7: a leg base is admissible only at or below 0 °C bare feels-like."""
    request = params(occasion="casual")
    mild = build_day_context(make_forecast(temps=12.0, wind=5.0), request)
    assert mild.leg_base_allowed is False
    assert build_candidates(wardrobe, request, mild).leg_base == ()
    freezing = build_day_context(make_forecast(temps=-4.0, wind=5.0), request)
    assert freezing.leg_base_allowed is True
    assert len(build_candidates(wardrobe, request, freezing).leg_base) == 1


# --------------------------------------------------------------------------
# FR-6.4 — S_thermal aggregation
# --------------------------------------------------------------------------


def test_fr6_thermal_score_weights_commute_hours_and_reserves_the_worst_hour():
    request = params(wear_window=(7, 9), commute_hours=(7,))
    ctx = build_day_context(make_forecast(temps=12.0), request)
    assert [hc.weight for hc in ctx.hours] == [EXPOSURE_COMMUTE, 1.0]
    scores = [0.5, 1.0]
    expected = 0.75 * ((3.0 * 0.5 + 1.0 * 1.0) / 4.0) + 0.25 * 0.5
    assert thermal_score(scores, ctx) == pytest.approx(expected)


def test_fr6_thermal_score_is_one_only_when_every_hour_is_in_band():
    request = params(wear_window=(7, 10))
    ctx = build_day_context(make_forecast(temps=12.0), request)
    assert thermal_score([1.0, 1.0, 1.0], ctx) == pytest.approx(1.0)
    assert thermal_score([1.0, 1.0, 0.0], ctx) < 0.75


def test_fr6_empty_wear_window_is_rejected():
    request = params(wear_window=(7, 8))
    forecast = make_forecast(hours=23)
    ctx = build_day_context(forecast, request)
    assert len(ctx.hours) == 1
    with pytest.raises(InvalidParams):
        build_day_context(make_forecast(temps=12.0, hours=23), params(wear_window=(2, 3)))


def test_fr6_context_covers_only_wear_window_hours():
    request = params(wear_window=(7, 22))
    ctx = build_day_context(make_forecast(temps=list(range(24))), request)
    assert [hc.weather.hour for hc in ctx.hours] == list(range(7, 22))


def test_fr8_wardrobe_fixture_is_the_documented_shape():
    """Guard the fixture EVALS.md §5.2's arithmetic depends on."""
    w = small_wardrobe()
    assert len(w) == 26
    roles = [g.layer_role for g in w]
    assert roles.count("base") == 6
    assert roles.count("mid") == 4
    assert roles.count("outer") == 4
    assert roles.count("bottom") == 5
    assert roles.count("leg_base") == 1
    assert roles.count("footwear") == 3
    assert roles.count("accessory") == 3
