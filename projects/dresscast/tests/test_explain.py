"""FR-15: one case per line class, the iff-emission invariant, byte stability."""

from __future__ import annotations

from itertools import pairwise

from conftest import NOW, make_forecast, params
from dresscast.engine.assemble import recommend
from dresscast.engine.explain import LINE_CLASSES, render_plan_table
from dresscast.engine.models import WearHistory


def _outfit(wardrobe, forecast, history=None, **kwargs):
    rec = recommend(wardrobe, forecast, history or WearHistory.empty(), params(**kwargs), now=NOW)
    return rec, rec.outfits[0]


def _classes(outfit) -> list[str]:
    return [line.line_class for line in outfit.reasoning]


def test_fr15_line_classes_appear_in_the_table_order(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    order = [LINE_CLASSES.index(c) for c in _classes(outfit)]
    assert order == sorted(order)


def test_fr15_day_thermal_and_palette_and_variety_always_appear(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    classes = _classes(outfit)
    assert classes.count("day_thermal") == 1
    assert classes.count("palette") == 1
    assert classes.count("variety") == 1


def test_fr15_no_line_is_emitted_for_a_trigger_that_did_not_fire(wardrobe):
    """A mild, dry, calm, low-UV day emits only the three unconditional lines."""
    calm = make_forecast(temps=16.0, wind=5.0, humidity=50.0, uv=1.0)
    _, outfit = _outfit(wardrobe, calm, occasion="casual")
    assert set(_classes(outfit)) <= {"day_thermal", "palette", "variety", "layer_change"}
    assert "rain" not in _classes(outfit)
    assert "wind" not in _classes(outfit)
    assert "uv" not in _classes(outfit)
    assert "cold_extremities" not in _classes(outfit)
    assert "wardrobe_limit" not in _classes(outfit)
    assert "compromise" not in _classes(outfit)


def test_fr15_layer_change_lines_match_the_plan_segments(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    worn = [tuple(e.worn_slots) for e in outfit.hour_plan]
    boundaries = sum(1 for a, b in pairwise(worn) if a != b)
    assert _classes(outfit).count("layer_change") == boundaries
    for line in outfit.reasoning:
        if line.line_class == "layer_change":
            assert ":00" in line.text


def test_fr15_layer_change_names_what_is_carried(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    changes = [line for line in outfit.reasoning if line.line_class == "layer_change"]
    assert changes
    assert any("carrying" in line.text for line in changes)


def test_fr15_wardrobe_limit_reports_the_signed_shortfall(wardrobe, winter_calm):
    _, outfit = _outfit(wardrobe, winter_calm, occasion="casual")
    lines = [line for line in outfit.reasoning if line.line_class == "wardrobe_limit"]
    assert len(lines) == 1
    assert "colder than anything you own" in lines[0].text
    assert "clo short" in lines[0].text
    clamped = {e.clamped for e in outfit.hour_plan}
    assert clamped == {"wardrobe_ceiling"}


def test_fr15_wardrobe_limit_is_one_line_per_maximal_clamped_run(wardrobe):
    """A day that dips below the ceiling mid-afternoon yields two runs."""
    temps = [-6.0] * 24
    for hour in range(12, 16):
        temps[hour] = 14.0
    forecast = make_forecast(temps=temps, wind=5.0, humidity=70.0)
    _, outfit = _outfit(wardrobe, forecast, occasion="casual")
    runs = 0
    previous = None
    for entry in outfit.hour_plan:
        if entry.clamped == "wardrobe_ceiling" and previous != "wardrobe_ceiling":
            runs += 1
        previous = entry.clamped
    assert runs == _classes(outfit).count("wardrobe_limit")


def test_fr15_rain_line_names_the_window_class_and_cover(wardrobe, summer_thunderstorm):
    _, outfit = _outfit(wardrobe, summer_thunderstorm, occasion="casual")
    lines = [line for line in outfit.reasoning if line.line_class == "rain"]
    assert len(lines) == 1
    assert "14:00-17:00" in lines[0].text
    assert "heavy" in lines[0].text
    assert "yellow-rain-shell" in lines[0].text


def test_fr15_wind_line_only_when_the_penalty_fired(wardrobe):
    windy = make_forecast(temps=12.0, wind=40.0, humidity=60.0)
    _, outfit = _outfit(wardrobe, windy, occasion="casual")
    if outfit.scores.protection < 1.0:
        assert "wind" in _classes(outfit)
        line = next(line for line in outfit.reasoning if line.line_class == "wind")
        assert "40 km/h" in line.text
    else:
        assert "wind" not in _classes(outfit)


def test_fr15_cold_extremities_names_the_accessories_or_the_gap(wardrobe, winter_calm):
    _, outfit = _outfit(wardrobe, winter_calm, occasion="casual")
    line = next(line for line in outfit.reasoning if line.line_class == "cold_extremities")
    assert "black-leather-gloves" in line.text
    assert "gray-wool-hat" in line.text
    assert "no scarf in the wardrobe" in line.text


def test_fr15_uv_line_names_the_absent_sun_accessory(wardrobe, summer_thunderstorm):
    _, outfit = _outfit(wardrobe, summer_thunderstorm, occasion="casual")
    line = next(line for line in outfit.reasoning if line.line_class == "uv")
    assert "no sunglasses in the wardrobe" in line.text


def test_fr15_palette_line_reports_neutrals_and_harmony(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    line = next(line for line in outfit.reasoning if line.line_class == "palette")
    assert "harmony" in line.text


def test_fr15_variety_line_names_the_least_fresh_item(wardrobe, spring_swing):
    history = WearHistory(last_worn={"f3-boots": "2026-04-11"}, yesterday_sets=())
    _, outfit = _outfit(wardrobe, spring_swing, history, occasion="casual")
    line = next(line for line in outfit.reasoning if line.line_class == "variety")
    if "f3-boots" in {i.garment_id for i in outfit.items}:
        assert "brown-leather-boots" in line.text
        assert "3 days ago" in line.text
    else:
        assert "worn before" in line.text


def test_fr15_rendering_is_byte_stable_across_runs(wardrobe, spring_swing):
    _, first = _outfit(wardrobe, spring_swing, occasion="casual")
    _, second = _outfit(wardrobe, spring_swing, occasion="casual")
    assert [x.as_dict() for x in first.reasoning] == [x.as_dict() for x in second.reasoning]


def test_fr15_reasoning_never_leaks_garment_notes(wardrobe, spring_swing):
    annotated = [
        g.model_copy(update={"notes": "SECRET-NOTE"}) if g.id == "f3-boots" else g for g in wardrobe
    ]
    _, outfit = _outfit(annotated, spring_swing, occasion="casual")
    assert all("SECRET-NOTE" not in line.text for line in outfit.reasoning)


def test_fr15_reason_lines_serialize_with_the_class_key(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    payload = outfit.reasoning[0].as_dict()
    assert set(payload) == {"class", "text"}


def test_fr18_plan_table_renders_one_row_per_hour(wardrobe, spring_swing):
    _, outfit = _outfit(wardrobe, spring_swing, occasion="casual")
    rows = render_plan_table(outfit.hour_plan)
    assert len(rows) == len(outfit.hour_plan) + 1
    assert rows[0].strip().startswith("hour")
