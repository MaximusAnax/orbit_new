"""FR-7: the two-method valuation ladder, which always reports its method."""

from __future__ import annotations

import pytest
from grailtrader.engine.valuation import value_garment
from grailtrader.models import (
    Category,
    ConditionGrade,
    GarmentStatus,
    ValuationMethod,
    ValuationReason,
)
from grailtrader_testkit import ERA_STRATUM, LEAF, build, flat_market, garment, week


def rising_market(**kwargs):
    return flat_market(
        weeks=31, per_week=6, level=lambda w: 1000.0 * (1.2 if w >= 10 else 1.0), **kwargs
    )


def test_fr7_repeat_sales_uses_the_index_ratio_and_the_condition_ratio(ctx):
    index = build(rising_market(), ctx, as_of_week=week(30))
    piece = garment(price=840.0, anchor_date=week(2))
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.method is ValuationMethod.REPEAT_SALES
    assert result.stratum_id == LEAF
    assert result.fair_value == pytest.approx(840.0 * 1.2)
    assert result.level_usd == pytest.approx(1200.0)
    assert result.unrealized_gain == pytest.approx(840.0 * 1.2 - 840.0)


def test_fr7_condition_change_rescales_the_value(ctx):
    index = build(rising_market(), ctx, as_of_week=week(30))
    piece = garment(
        price=840.0,
        anchor_date=week(2),
        condition=ConditionGrade.GOOD,
        anchor_condition=ConditionGrade.EXCELLENT,
    )
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.fair_value == pytest.approx(840.0 * 1.2 * 0.8)


def test_fr7_carries_the_anchor_week_back_to_the_nearest_earlier_point(ctx):
    listings = rising_market()
    index = build(listings, ctx, as_of_week=week(30))
    # week 2 is published; a Thursday anchor inside week 2 resolves to the same point.
    piece = garment(price=1000.0, anchor_date="2024-01-18")
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.method is ValuationMethod.REPEAT_SALES
    assert result.fair_value == pytest.approx(1200.0)


def test_fr7_falls_back_to_comp_based_when_the_anchor_predates_the_history(ctx):
    index = build(rising_market(), ctx, as_of_week=week(30))
    piece = garment(price=500.0, anchor_date="2020-01-06")
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.method is ValuationMethod.COMP_BASED
    assert result.stratum_id == LEAF
    assert result.fair_value == pytest.approx(1200.0)


def test_fr7_comp_based_restates_the_level_to_the_garments_condition(ctx):
    index = build(rising_market(), ctx, as_of_week=week(30))
    piece = garment(price=500.0, anchor_date="2020-01-06", condition=ConditionGrade.FAIR)
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.method is ValuationMethod.COMP_BASED
    assert result.fair_value == pytest.approx(1200.0 * 0.55)


def test_fr7_unavailable_no_index_when_nothing_on_the_path_is_indexed(ctx):
    index = build(rising_market(), ctx, as_of_week=week(30))
    piece = garment(brand_id="rick-owens", era_id="rick-owens:rick", price=800.0)
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.method is ValuationMethod.UNAVAILABLE
    assert result.reason is ValuationReason.NO_INDEX
    assert result.fair_value is None


def test_fr7_unavailable_stale_index_when_every_point_is_too_old(ctx):
    index = build(flat_market(weeks=6, per_week=6), ctx, as_of_week=week(40))
    piece = garment(price=800.0, anchor_date=week(1))
    result = value_garment(piece, index=index, as_of_week=week(40), ctx=ctx)
    assert result.method is ValuationMethod.UNAVAILABLE
    assert result.reason is ValuationReason.STALE_INDEX


def test_fr7_unavailable_no_index_at_anchor_when_only_a_parent_is_fresh(ctx):
    listings = [
        *flat_market(weeks=6, per_week=6, prefix="o"),
        *flat_market(weeks=31, per_week=6, level=400.0, category=Category.KNITWEAR, prefix="k"),
    ]
    index = build(listings, ctx, as_of_week=week(30))
    piece = garment(price=800.0, anchor_date="2020-01-06")
    result = value_garment(piece, index=index, as_of_week=week(25), ctx=ctx)
    assert index.most_specific_fresh(LEAF, week(25), 8)[0] == ERA_STRATUM
    assert result.method is ValuationMethod.UNAVAILABLE
    assert result.reason is ValuationReason.NO_INDEX_AT_ANCHOR
    assert result.level_usd is None


def test_fr7_uses_the_most_specific_ancestor_covering_both_weeks(ctx):
    listings = [
        *flat_market(weeks=6, per_week=6, prefix="o"),
        *flat_market(weeks=31, per_week=6, level=400.0, category=Category.KNITWEAR, prefix="k"),
    ]
    index = build(listings, ctx, as_of_week=week(30))
    piece = garment(price=800.0, anchor_date=week(1))
    result = value_garment(piece, index=index, as_of_week=week(25), ctx=ctx)
    assert result.method is ValuationMethod.REPEAT_SALES
    assert result.stratum_id == ERA_STRATUM


def test_fr7_watching_garments_anchor_on_the_reference_pair(ctx):
    index = build(rising_market(), ctx, as_of_week=week(30))
    piece = garment(price=900.0, anchor_date=week(2), status=GarmentStatus.WATCHING)
    assert piece.anchor_price == 900.0
    assert piece.anchor_date == week(2)
    result = value_garment(piece, index=index, as_of_week=week(20), ctx=ctx)
    assert result.method is ValuationMethod.REPEAT_SALES
    assert result.fair_value == pytest.approx(900.0 * 1.2)
