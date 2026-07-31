"""FR-16: the wardrobe-free day brief (US-9)."""

from __future__ import annotations

import pytest
from conftest import diurnal, make_forecast, params
from dresscast.engine.comfort import build_day_context, day_brief, layer_archetype
from dresscast.engine.models import ARCHETYPE_NAMES, PLAN_DP


def test_fr16_brief_needs_no_wardrobe_at_all(spring_swing):
    brief = day_brief(spring_swing, params())
    assert brief.date == "2026-04-14"
    assert len(brief.hours) == 15
    assert brief.required_clo_max > brief.required_clo_min


def test_fr16_brief_covers_exactly_the_wear_window(spring_swing):
    brief = day_brief(spring_swing, params(wear_window=(9, 12)))
    assert [h.hour for h in brief.hours] == [9, 10, 11]


def test_fr16_required_clo_is_computed_at_windproofness_zero(spring_swing):
    request = params()
    brief = day_brief(spring_swing, request)
    ctx = build_day_context(spring_swing, request)
    for entry, hc in zip(brief.hours, ctx.hours, strict=True):
        assert entry.bare_feels_c == pytest.approx(round(hc.bare_feels_c, PLAN_DP))
        assert entry.required_clo == pytest.approx(round(hc.required_by_w[0], PLAN_DP))


def test_fr16_archetype_brackets_the_required_clo():
    assert layer_archetype(0.0) == "base"
    assert layer_archetype(0.54) == "base"
    assert layer_archetype(0.55) == "base+mid"
    assert layer_archetype(1.09) == "base+mid"
    assert layer_archetype(1.10) == "base+mid+shell"
    assert layer_archetype(1.69) == "base+mid+shell"
    assert layer_archetype(1.70) == "base+2mid+insulated shell"
    assert layer_archetype(3.0) == ARCHETYPE_NAMES[-1]


def test_fr16_brief_reports_the_archetype_range_in_order(winter_calm):
    brief = day_brief(winter_calm, params())
    assert brief.archetype_range
    order = [ARCHETYPE_NAMES.index(a) for a in brief.archetype_range]
    assert order == sorted(order)


def test_fr16_rain_cover_class_per_hour(summer_thunderstorm):
    brief = day_brief(summer_thunderstorm, params())
    wet = [h for h in brief.hours if h.rain_required]
    dry = [h for h in brief.hours if not h.rain_required]
    assert [h.hour for h in wet] == [14, 15, 16, 17]
    assert all(h.rain_cover_class == "heavy" for h in wet)
    assert all(h.rain_cover_class == "none" for h in dry)


def test_fr16_soft_rain_hours_carry_a_class_without_a_hard_requirement():
    prob = [0.0] * 24
    prob[12] = 0.35
    forecast = make_forecast(temps=12.0, precip_prob=prob, precip_mmh=1.0)
    brief = day_brief(forecast, params())
    soft = next(h for h in brief.hours if h.hour == 12)
    assert soft.rain_cover_class == "light"
    assert soft.rain_required is False


def test_fr16_cold_extremity_advisory(winter_calm, spring_swing):
    cold = day_brief(winter_calm, params())
    assert [a.kind for a in cold.advisories].count("cold_extremities") == 1
    mild = day_brief(make_forecast(temps=18.0, wind=0.0), params())
    assert "cold_extremities" not in [a.kind for a in mild.advisories]


def test_fr16_wind_advisory_fires_at_thirty_kmh():
    assert "wind" in [
        a.kind for a in day_brief(make_forecast(temps=12.0, wind=30.0), params()).advisories
    ]
    assert "wind" not in [
        a.kind for a in day_brief(make_forecast(temps=12.0, wind=29.0), params()).advisories
    ]


def test_fr16_uv_advisory_fires_at_index_six(summer_thunderstorm):
    advisories = day_brief(summer_thunderstorm, params()).advisories
    uv = [a for a in advisories if a.kind == "uv"]
    assert len(uv) == 1
    assert uv[0].value == pytest.approx(7.0)


def test_fr16_every_brief_float_is_quantized(spring_swing):
    brief = day_brief(spring_swing, params())
    for entry in brief.hours:
        for value in (
            entry.bare_feels_c,
            entry.required_clo,
            entry.temp_c,
            entry.wind_kmh,
            entry.uv_index,
        ):
            assert value == round(value, PLAN_DP)


def test_fr16_met_changes_what_the_day_demands(spring_swing):
    lazy = day_brief(spring_swing, params(met=1.2))
    brisk = day_brief(spring_swing, params(met=2.2))
    assert lazy.required_clo_max > brisk.required_clo_max


def test_fr16_brief_is_deterministic(spring_swing):
    a = day_brief(spring_swing, params())
    b = day_brief(spring_swing, params())
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_fr16_dst_days_are_briefed_without_complaint():
    spring = day_brief(make_forecast(temps=diurnal(4.0, 12.0, hours=23), hours=23), params())
    autumn = day_brief(make_forecast(temps=diurnal(4.0, 12.0, hours=25), hours=25), params())
    assert len(spring.hours) >= 14
    assert len(autumn.hours) >= 15
