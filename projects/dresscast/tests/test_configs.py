"""FR-7: configuration enumeration, LIFO shed order, hysteresis and smoothing."""

from __future__ import annotations

from itertools import pairwise

import pytest
from conftest import diurnal, make_forecast, params
from dresscast.engine.comfort import (
    build_day_context,
    compress_plan,
    hourly_plan,
    layer_configs,
    select_plan,
    target_table,
)
from dresscast.engine.models import (
    MAX_CONFIG_CHANGES,
    Band,
    CoreOutfit,
)


def _outfit(wardrobe, base, bottom, mids=(), outer=None, leg_base=None, footwear="f3-boots"):
    by_id = {g.id: g for g in wardrobe}
    picked = sorted((by_id[m] for m in mids), key=lambda g: (g.clo, g.id))
    return CoreOutfit(
        base=by_id[base],
        bottom=by_id[bottom],
        mids=tuple(picked),
        outer=by_id[outer] if outer else None,
        leg_base=by_id[leg_base] if leg_base else None,
        footwear=by_id[footwear],
    )


def test_fr7_configuration_count_is_two_times_mids_plus_one(wardrobe):
    bare = _outfit(wardrobe, "b5-oxford", "p4-jeans")
    assert len(layer_configs(bare)) == 1
    one_mid = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m3-thick",))
    assert len(layer_configs(one_mid)) == 2
    full = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m1-thin", "m3-thick"), outer="o3-coat")
    assert len(layer_configs(full)) == 6


def test_fr7_configurations_are_ordered_and_total_in_icl(wardrobe):
    full = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m1-thin", "m3-thick"), outer="o3-coat")
    icls = [c.icl for c in layer_configs(full)]
    assert icls == sorted(icls)
    assert len({round(i, 9) for i in icls}) == len(icls)


def test_fr7_mids_are_shed_outermost_first_thickest_first(wardrobe):
    """D6's LIFO chain: ``mid_2`` is the thicker mid and comes off first."""
    full = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m1-thin", "m3-thick"))
    configs = layer_configs(full)
    worn = [c.worn_slots for c in configs]
    assert tuple(s for s in worn[0] if s in {"base", "bottom", "footwear"}) == (
        "base",
        "bottom",
        "footwear",
    )
    assert "mid_2" not in worn[0]
    assert "mid_1" in worn[1] and "mid_2" not in worn[1]
    assert "mid_1" in worn[2] and "mid_2" in worn[2]
    assert full.mids[0].clo <= full.mids[1].clo


def test_fr7_outermost_worn_layer_sets_windproofness_and_cover(wardrobe):
    full = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m3-thick",), outer="o1-shell")
    configs = layer_configs(full)
    with_outer = [c for c in configs if "outer" in c.worn_slots]
    without = [c for c in configs if "outer" not in c.worn_slots]
    assert all(c.windproofness == 2 for c in with_outer)
    assert all(c.cover == 2 for c in with_outer)
    assert all(c.windproofness == 0 for c in without)
    assert all(c.cover == 0 for c in without)


def test_fr7_slot_items_order_puts_mid_1_before_mid_2(wardrobe):
    full = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m1-thin", "m3-thick"))
    slots = [slot for slot, _ in full.slot_items()]
    assert slots.index("mid_1") < slots.index("mid_2")
    mid_1 = dict(full.slot_items())["mid_1"]
    mid_2 = dict(full.slot_items())["mid_2"]
    assert (mid_2.clo, mid_2.id) > (mid_1.clo, mid_1.id)


# --------------------------------------------------------------------------
# Smoothing
# --------------------------------------------------------------------------


def _swing_plan(wardrobe, **kwargs):
    request = params(occasion="casual", **kwargs)
    forecast = make_forecast(temps=diurnal(5.0, 18.0), wind=10.0, humidity=55.0)
    ctx = build_day_context(forecast, request)
    outfit = _outfit(
        wardrobe, "b5-oxford", "p4-jeans", mids=("m1-thin", "m3-thick"), outer="o3-coat"
    )
    configs = layer_configs(outfit)
    band = Band(ceiling=2.04, floor=tuple(0.311 for _ in ctx.hours))
    plan = select_plan(configs, ctx, band, False)
    return ctx, outfit, configs, band, plan


def test_fr7_swing_day_plan_has_at_least_two_configurations(wardrobe):
    _, _, _, _, plan = _swing_plan(wardrobe)
    assert plan is not None
    assert len({plan.chosen}) >= 1
    assert len(set(plan.chosen)) >= 2


def test_fr7_plan_never_exceeds_max_config_changes(wardrobe):
    _, _, _, _, plan = _swing_plan(wardrobe)
    assert plan.changes <= MAX_CONFIG_CHANGES
    transitions = sum(1 for a, b in pairwise(plan.chosen) if a != b)
    assert transitions <= MAX_CONFIG_CHANGES


def test_fr7_min_dwell_hours_is_respected(wardrobe):
    """Every configuration segment except possibly the last runs ≥ 2 hours."""
    _, _, _, _, plan = _swing_plan(wardrobe)
    lengths: list[int] = []
    run = 1
    for a, b in pairwise(plan.chosen):
        if a == b:
            run += 1
        else:
            lengths.append(run)
            run = 1
    lengths.append(run)
    assert all(length >= 2 for length in lengths[:-1])


def test_fr7_hysteresis_suppresses_a_marginal_switch(wardrobe):
    """A candidate that improves deviation by < 0.10 clo must not be taken."""
    request = params(occasion="casual", wear_window=(7, 12))
    forecast = make_forecast(temps=[12.0] * 24, wind=5.0, humidity=55.0)
    ctx = build_day_context(forecast, request)
    outfit = _outfit(wardrobe, "b5-oxford", "p4-jeans", mids=("m1-thin",))
    configs = layer_configs(outfit)
    band = Band(ceiling=2.04, floor=tuple(0.311 for _ in ctx.hours))
    plan = select_plan(configs, ctx, band, False)
    assert len(set(plan.chosen)) == 1


def test_fr7_segment_compression_is_lossless(wardrobe):
    ctx, outfit, configs, band, plan = _swing_plan(wardrobe)
    entries = hourly_plan(outfit, ctx, band, configs, plan, [1.0] * len(ctx.hours), False)
    segments = compress_plan(entries)
    rebuilt: list[list[str]] = []
    for segment in segments:
        span = sum(1 for e in entries if segment.start_seq <= e.seq <= segment.end_seq)
        rebuilt.extend([segment.worn_slots] * span)
    assert rebuilt == [e.worn_slots for e in entries]
    assert len(segments) == 1 + sum(1 for a, b in pairwise(entries) if a.worn_slots != b.worn_slots)


def test_fr7_shed_layers_are_marked_as_carried(wardrobe):
    ctx, outfit, configs, band, plan = _swing_plan(wardrobe)
    entries = hourly_plan(outfit, ctx, band, configs, plan, [1.0] * len(ctx.hours), False)
    shed = [e for e in entries if "shed_layer" in e.notes]
    assert shed, "a 5→18 °C day must shed something"
    after = entries[entries.index(shed[0]) :]
    assert all(e.carried_slots for e in after)
    assert all("carrying" in e.notes for e in after)


def test_fr7_rain_hours_force_the_cover_on_even_against_hysteresis(wardrobe):
    """HC-6 outranks plan smoothness: the shell goes on for the downpour."""
    request = params(occasion="casual")
    prob = [0.05] * 24
    mmh = [0.0] * 24
    for hour in range(14, 18):
        prob[hour] = 0.7
        mmh[hour] = 12.0
    forecast = make_forecast(temps=diurnal(22.0, 30.0), precip_prob=prob, precip_mmh=mmh, wind=12.0)
    ctx = build_day_context(forecast, request)
    outfit = _outfit(wardrobe, "b1-tee", "p1-shorts", outer="o1-shell", footwear="f1-sandals")
    configs = layer_configs(outfit)
    band = Band(ceiling=2.04, floor=tuple(0.311 for _ in ctx.hours))
    plan = select_plan(configs, ctx, band, False)
    assert plan is not None
    for pos, hc in enumerate(ctx.hours):
        if hc.hard_rain:
            assert configs[plan.chosen[pos]].cover >= hc.needed_cover


def test_fr7_outfit_without_adequate_cover_has_no_feasible_plan(wardrobe):
    """The wool coat is waterproofness 1 — not enough for 12 mm/h."""
    request = params(occasion="casual")
    prob = [0.7] * 24
    mmh = [12.0] * 24
    forecast = make_forecast(temps=12.0, precip_prob=prob, precip_mmh=mmh, wind=12.0)
    ctx = build_day_context(forecast, request)
    outfit = _outfit(wardrobe, "b5-oxford", "p4-jeans", outer="o3-coat")
    band = Band(ceiling=2.04, floor=tuple(0.311 for _ in ctx.hours))
    assert select_plan(layer_configs(outfit), ctx, band, False) is None


def test_fr7_target_table_matches_per_config_computation(wardrobe):
    request = params(occasion="casual")
    ctx = build_day_context(make_forecast(temps=diurnal(-2.0, 6.0), wind=25.0), request)
    band = Band(ceiling=1.5, floor=tuple(0.4 for _ in ctx.hours))
    table = target_table(ctx, band)
    for hc in ctx.hours:
        for w in range(3):
            expected, _ = band.clamp(hc.required_by_w[w], hc.index)
            assert table.of(hc.index, w) == pytest.approx(expected)
