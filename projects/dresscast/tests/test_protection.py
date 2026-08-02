"""FR-9: intensity classes, the umbrella wind cap, ``S_protect``, attachment."""

from __future__ import annotations

import pytest
from conftest import diurnal, garment, make_forecast, params
from dresscast.engine.comfort import (
    build_day_context,
    intensity_class,
    layer_configs,
    select_plan,
)
from dresscast.engine.models import (
    EXPOSURE_COMMUTE,
    PENALTY_RAIN_UNCOVERED,
    PENALTY_SOFT_RAIN,
    PENALTY_WIND_PARTIAL,
    PENALTY_WIND_UNBLOCKED,
    Band,
    CoreOutfit,
)
from dresscast.engine.protection import (
    ProtectionEval,
    attach_accessories,
    cover_adequate,
    protect_score,
    rain_hours,
    required_cover,
    select_umbrella,
    soft_rain_hours,
    triggered_classes,
)


def _outfit(wardrobe, base, bottom, outer=None, footwear="f3-boots"):
    by_id = {g.id: g for g in wardrobe}
    return CoreOutfit(
        base=by_id[base],
        bottom=by_id[bottom],
        mids=(),
        outer=by_id[outer] if outer else None,
        leg_base=None,
        footwear=by_id[footwear],
    )


def _ctx(**kwargs):
    request = params(occasion="casual")
    return build_day_context(make_forecast(**kwargs), request)


def _flat_band(ctx):
    return Band(ceiling=2.04, floor=tuple(0.311 for _ in ctx.hours))


# --------------------------------------------------------------------------
# Intensity classes and adequacy
# --------------------------------------------------------------------------


def test_fr9_intensity_classes_follow_wmo_bands():
    assert intensity_class(0.0) == "light"
    assert intensity_class(2.49) == "light"
    assert intensity_class(2.5) == "moderate"
    assert intensity_class(10.0) == "moderate"
    assert intensity_class(10.01) == "heavy"


def test_fr9_hard_and_soft_rain_hours_split_at_the_pop_thresholds():
    prob = [0.0] * 24
    prob[8] = 0.29
    prob[9] = 0.30
    prob[10] = 0.49
    prob[11] = 0.50
    ctx = _ctx(precip_prob=prob, precip_mmh=1.0)
    assert [h.weather.hour for h in rain_hours(ctx)] == [11]
    assert [h.weather.hour for h in soft_rain_hours(ctx)] == [9, 10]


def test_fr9_required_cover_by_class():
    light = _ctx(precip_prob=0.9, precip_mmh=1.0).hours[0]
    moderate = _ctx(precip_prob=0.9, precip_mmh=5.0).hours[0]
    heavy = _ctx(precip_prob=0.9, precip_mmh=20.0).hours[0]
    soft = _ctx(precip_prob=0.35, precip_mmh=20.0).hours[0]
    assert required_cover(light) == 1
    assert required_cover(moderate) == 2
    assert required_cover(heavy) == 2
    assert required_cover(soft) == 1


def test_fr9_umbrella_is_invalid_for_heavy_rain(wardrobe):
    heavy = _ctx(precip_prob=0.9, precip_mmh=20.0, wind=5.0).hours[0]
    assert heavy.umbrella_ok is False
    naked = layer_configs(_outfit(wardrobe, "b5-oxford", "p4-jeans"))[0]
    assert cover_adequate(naked, heavy, umbrella=True) is False


def test_fr9_umbrella_is_invalid_at_or_above_35_kmh(wardrobe):
    windy = _ctx(precip_prob=0.9, precip_mmh=1.0, wind=35.0).hours[0]
    calm = _ctx(precip_prob=0.9, precip_mmh=1.0, wind=34.9).hours[0]
    assert windy.umbrella_ok is False
    assert calm.umbrella_ok is True
    naked = layer_configs(_outfit(wardrobe, "b5-oxford", "p4-jeans"))[0]
    assert cover_adequate(naked, windy, umbrella=True) is False
    assert cover_adequate(naked, calm, umbrella=True) is True


def test_fr9_worn_cover_satisfies_adequacy_without_an_umbrella(wardrobe):
    moderate = _ctx(precip_prob=0.9, precip_mmh=5.0, wind=5.0).hours[0]
    shell = layer_configs(_outfit(wardrobe, "b5-oxford", "p4-jeans", outer="o1-shell"))
    dressed = next(c for c in shell if "outer" in c.worn_slots)
    assert cover_adequate(dressed, moderate, umbrella=False) is True
    coat = layer_configs(_outfit(wardrobe, "b5-oxford", "p4-jeans", outer="o3-coat"))
    partial = next(c for c in coat if "outer" in c.worn_slots)
    assert partial.cover == 1
    assert cover_adequate(partial, moderate, umbrella=False) is False


# --------------------------------------------------------------------------
# S_protect
# --------------------------------------------------------------------------


def _score(wardrobe, ctx, base="b5-oxford", bottom="p4-jeans", outer=None, umbrella=False):
    outfit = _outfit(wardrobe, base, bottom, outer=outer)
    configs = layer_configs(outfit)
    plan = select_plan(configs, ctx, _flat_band(ctx), umbrella)
    assert plan is not None
    return protect_score(configs, plan.chosen, ctx, umbrella)


def test_fr9_dry_calm_day_scores_a_clean_one(wardrobe):
    ctx = _ctx(temps=12.0, wind=5.0)
    assert _score(wardrobe, ctx).score == pytest.approx(1.0)


def test_fr9_uncovered_soft_rain_costs_a_quarter_per_hour(wardrobe):
    prob = [0.0] * 24
    prob[12] = 0.35
    ctx = _ctx(temps=12.0, wind=5.0, precip_prob=prob, precip_mmh=1.0)
    result = _score(wardrobe, ctx)
    hour = next(i for i, hc in enumerate(ctx.hours) if hc.soft_rain)
    assert result.hour_scores[hour] == pytest.approx(1.0 - PENALTY_SOFT_RAIN)
    assert result.uncovered_soft_hours == (hour,)


def test_fr9_wind_penalties_depend_on_the_outermost_layer(wardrobe):
    ctx = _ctx(temps=12.0, wind=32.0)
    bare = _score(wardrobe, ctx)
    assert bare.hour_scores[0] == pytest.approx(1.0 - PENALTY_WIND_UNBLOCKED)
    coat = _score(wardrobe, ctx, outer="o3-coat")
    assert coat.hour_scores[0] == pytest.approx(1.0 - PENALTY_WIND_PARTIAL)
    shell = _score(wardrobe, ctx, outer="o1-shell")
    assert shell.hour_scores[0] == pytest.approx(1.0)
    assert shell.wind_penalty_fired is False


def test_fr9_wind_penalty_threshold_is_30_kmh(wardrobe):
    assert _score(wardrobe, _ctx(temps=12.0, wind=29.9)).score == pytest.approx(1.0)
    assert _score(wardrobe, _ctx(temps=12.0, wind=30.0)).score < 1.0


def test_fr9_protect_score_uses_the_same_exposure_weights_as_thermal(wardrobe):
    request = params(occasion="casual", wear_window=(7, 9), commute_hours=(7,))
    wind = [5.0] * 24
    wind[7] = 40.0
    ctx = build_day_context(make_forecast(temps=12.0, wind=wind), request)
    result = _score(wardrobe, ctx)
    expected = (EXPOSURE_COMMUTE * (1.0 - PENALTY_WIND_UNBLOCKED) + 1.0 * 1.0) / 4.0
    assert result.score == pytest.approx(expected)


def test_fr9_umbrella_carries_adequacy_when_nothing_worn_does(wardrobe):
    prob = [0.0] * 24
    prob[12] = 0.9
    ctx = _ctx(temps=12.0, wind=5.0, precip_prob=prob, precip_mmh=1.0)
    with_umbrella = _score(wardrobe, ctx, umbrella=True)
    assert with_umbrella.score == pytest.approx(1.0)
    assert with_umbrella.umbrella_hours


def test_fr9_uncovered_hard_rain_costs_half_an_hour_score(wardrobe):
    """Only reachable under FR-14's R3 — HC-6 blocks it in a normal run."""
    prob = [0.0] * 24
    prob[12] = 0.9
    ctx = _ctx(temps=12.0, wind=5.0, precip_prob=prob, precip_mmh=1.0)
    configs = layer_configs(_outfit(wardrobe, "b5-oxford", "p4-jeans"))
    result = protect_score(configs, [0] * len(ctx.hours), ctx, False)
    hour = next(i for i, hc in enumerate(ctx.hours) if hc.hard_rain)
    assert result.hour_scores[hour] == pytest.approx(1.0 - PENALTY_RAIN_UNCOVERED)
    assert result.uncovered_rain_hours == (hour,)


# --------------------------------------------------------------------------
# Accessory attachment
# --------------------------------------------------------------------------


def _attach(wardrobe, ctx, protection=None, occasion="casual", base="b5-oxford"):
    outfit = _outfit(wardrobe, base, "p4-jeans")
    accessories = [g for g in wardrobe if g.layer_role == "accessory"]
    empty = ProtectionEval(
        score=1.0,
        hour_scores=tuple(1.0 for _ in ctx.hours),
        umbrella_hours=(),
        uncovered_rain_hours=(),
        uncovered_soft_hours=(),
        wind_penalty_hours=(),
    )
    return attach_accessories(outfit, accessories, ctx, protection or empty, occasion)


def test_fr9_cold_extremities_attach_gloves_and_hat(wardrobe):
    ctx = _ctx(temps=diurnal(-6.0, -2.0), wind=5.0)
    plan = _attach(wardrobe, ctx)
    assert plan.classes == {"gloves", "hat"}
    assert [g for g, _ in plan.gaps] == ["scarf"]


def test_fr9_no_cold_trigger_above_five_degrees(wardrobe):
    ctx = _ctx(temps=12.0, wind=0.0)
    plan = _attach(wardrobe, ctx)
    assert plan.attachments == ()
    assert plan.gaps == ()


def test_fr9_uv_trigger_is_confined_to_the_10_to_16_window(wardrobe):
    uv = [0.0] * 24
    uv[8] = 9.0
    ctx = _ctx(temps=20.0, uv=uv)
    assert ctx.uv_advisory is False
    uv2 = [0.0] * 24
    uv2[13] = 9.0
    ctx2 = _ctx(temps=20.0, uv=uv2)
    assert ctx2.uv_advisory is True
    plan = _attach(wardrobe, ctx2)
    assert plan.classes == set()
    assert [g for g, _ in plan.gaps] == ["sunglasses"]


def test_fr9_accessories_fill_slots_in_ascending_garment_id_order(wardrobe):
    ctx = _ctx(temps=diurnal(-6.0, -2.0), wind=5.0)
    plan = _attach(wardrobe, ctx)
    ids = [a.garment_id for a in plan.attachments]
    assert ids == sorted(ids)


def test_fr9_attachment_priority_caps_at_four_classes(wardrobe):
    """Umbrella > gloves > hat > scarf > sunglasses when more than four fire."""
    ctx = _ctx(temps=diurnal(-6.0, -2.0), wind=5.0)
    protection = ProtectionEval(
        score=1.0,
        hour_scores=tuple(1.0 for _ in ctx.hours),
        umbrella_hours=(0,),
        uncovered_rain_hours=(),
        uncovered_soft_hours=(),
        wind_penalty_hours=(),
    )
    order = [cls for cls, _ in triggered_classes(ctx, protection)]
    assert order == ["umbrella", "gloves", "hat", "scarf"]
    plan = _attach(wardrobe, ctx, protection)
    assert plan.classes == {"umbrella", "gloves", "hat"}
    assert len(plan.attachments) <= 4


def test_fr9_accessory_must_list_the_requested_occasion(wardrobe):
    ctx = _ctx(temps=diurnal(-6.0, -2.0), wind=5.0)
    plan = _attach(wardrobe, ctx, occasion="formal")
    assert plan.classes == set()


def test_fr9_accessory_must_be_clean(wardrobe):
    dirty = [
        g.model_copy(update={"status": "dirty"}) if g.id == "a3-gloves" else g for g in wardrobe
    ]
    ctx = _ctx(temps=diurnal(-6.0, -2.0), wind=5.0)
    plan = _attach(dirty, ctx)
    assert "gloves" not in plan.classes
    assert ("gloves", plan.gaps[0][1]) in plan.gaps


def test_fr9_accessory_formality_must_keep_hc4_intact(wardrobe):
    """A formality-5 scarf cannot join a formality 2-3 outfit."""
    loud = [
        *wardrobe,
        garment(
            "a9-scarf",
            "black-tie-scarf",
            "scarf",
            colors=("black",),
            formality=5,
            accessory_class="scarf",
            clo=0.0,
            occasions=("casual", "work"),
        ),
    ]
    ctx = _ctx(temps=diurnal(-6.0, -2.0), wind=5.0)
    plan = _attach(loud, ctx)
    assert "scarf" not in plan.classes


def test_fr9_umbrella_selection_is_deterministic_and_hc4_compatible(wardrobe):
    outfit = _outfit(wardrobe, "b5-oxford", "p4-jeans")
    accessories = [g for g in wardrobe if g.layer_role == "accessory"]
    pick = select_umbrella(accessories, outfit, "casual")
    assert pick is not None and pick.id == "a1-umbrella"
    unlisted = [
        g.model_copy(update={"occasions": ["formal"]}) if g.id == "a1-umbrella" else g
        for g in accessories
    ]
    assert select_umbrella(unlisted, outfit, "casual") is None
