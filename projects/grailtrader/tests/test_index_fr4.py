"""FR-4: index construction (hard part A) — leaf windows and parent chain-linking."""

from __future__ import annotations

import math
from itertools import pairwise

import pytest
from grailtrader.engine.index import build_index
from grailtrader.engine.ingest import normalize_listings
from grailtrader.models import Category, ConditionGrade, ListingSource, ListingStatus, RawListing
from grailtrader_testkit import (
    BRAND,
    ERA,
    ERA_STRATUM,
    LEAF,
    START,
    build,
    flat_market,
    sold_listing,
    week,
)

KNITWEAR = f"{BRAND}/helmut/knitwear"


def test_fr4_leaf_level_is_the_window_median_of_condition_adjusted_prices(ctx):
    listings = [
        sold_listing(external_id="a", price=1000.0, week_key=START),
        sold_listing(
            external_id="b",
            price=1250.0,
            week_key=START,
            condition=ConditionGrade.NEW,
            platform_label="NWT",
        ),
        sold_listing(
            external_id="c",
            price=800.0,
            week_key=START,
            condition=ConditionGrade.GOOD,
            platform_label="Used",
        ),
        sold_listing(
            external_id="d",
            price=550.0,
            week_key=START,
            condition=ConditionGrade.FAIR,
            platform_label="Very Worn",
        ),
        sold_listing(
            external_id="e",
            price=350.0,
            week_key=START,
            condition=ConditionGrade.POOR,
            platform_label="Poor",
        ),
    ]
    index = build(listings, ctx, as_of_week=START)
    point = index.point_at(LEAF, START)
    assert point is not None
    assert point.level_usd == pytest.approx(1000.0)
    assert point.index_value == pytest.approx(100.0)
    assert point.n_sales == 5


def test_fr4_min_sales_gate_leaves_the_week_unwritten(ctx):
    listings = flat_market(weeks=3, per_week=4)
    index = build(listings, ctx, as_of_week=week(2))
    assert index.point_at(LEAF, START) is None  # 4 sales in the window < min_sales = 5
    assert index.point_at(LEAF, week(1)) is not None  # 8 sales in the trailing window


def test_fr4_trailing_window_is_four_weeks(ctx):
    listings = [
        *flat_market(weeks=1, per_week=6, level=1000.0, prefix="old"),
        *flat_market(weeks=1, per_week=6, level=2000.0, prefix="new", start=week(4)),
    ]
    index = build(listings, ctx, as_of_week=week(9))
    # week 3 still sees the old sales; week 4 sees only the new ones.
    assert index.point_at(LEAF, week(3)).level_usd == pytest.approx(1000.0)
    assert index.point_at(LEAF, week(4)).level_usd == pytest.approx(2000.0)
    assert index.point_at(LEAF, week(7)).level_usd == pytest.approx(2000.0)
    assert index.point_at(LEAF, week(8)) is None  # window has drained below min_sales


def test_fr4_index_is_based_at_100_on_the_first_eligible_week(ctx):
    listings = flat_market(weeks=12, per_week=6, level=lambda w: 1000.0 * (1.10 if w >= 6 else 1.0))
    index = build(listings, ctx, as_of_week=week(11))
    points = index.points(LEAF)
    assert points[0].index_value == pytest.approx(100.0)
    assert points[-1].index_value == pytest.approx(110.0)
    assert points[-1].level_usd == pytest.approx(1100.0)


def test_fr4_asking_prices_never_enter_the_index(ctx):
    listings = flat_market(weeks=2, per_week=6)
    raws = [
        RawListing(
            source=ListingSource.FIXTURE,
            external_id=f"ask-{i}",
            brand_ref=BRAND,
            era_ref=ERA,
            category="outerwear",
            platform_condition="Excellent",
            status="active",
            listed_at=f"{START}T00:00:00Z",
            ask_price=99999.0,
        )
        for i in range(20)
    ]
    asks, _ = normalize_listings(raws, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert all(listing.status is ListingStatus.ACTIVE for listing in asks)
    index = build([*listings, *asks], ctx, as_of_week=week(1))
    assert index.point_at(LEAF, week(1)).level_usd == pytest.approx(1000.0)


def test_fr4_excluded_outliers_are_counted_and_inspectable(ctx):
    listings = [
        *flat_market(weeks=1, per_week=10),
        sold_listing(external_id="fake", price=200.0, week_key=START),
        sold_listing(external_id="mislabel", price=4000.0, week_key=START),
    ]
    index = build(listings, ctx, as_of_week=START)
    point = index.point_at(LEAF, START)
    assert point.n_sales == 10
    assert point.n_excluded == 2
    excluded = index.excluded_listing_ids(LEAF, START)
    assert len(excluded) == 2


def test_fr4_no_point_is_fabricated_and_staleness_is_a_query_time_distance(ctx):
    listings = flat_market(weeks=6, per_week=6)
    index = build(listings, ctx, as_of_week=week(20))
    assert index.point_at(LEAF, week(20)) is None
    carried, staleness = index.carried(LEAF, week(20))
    assert carried.week == week(8)  # last week the trailing window still held sales
    assert staleness == 12
    assert index.most_specific_fresh(LEAF, week(20), 8) is None
    assert index.most_specific_fresh(LEAF, week(12), 8) == (LEAF, carried, 4)


def test_fr4_parent_carries_no_level_and_no_exclusions(ctx):
    listings = [
        *flat_market(weeks=10, per_week=6, prefix="o"),
        *flat_market(weeks=10, per_week=6, level=400.0, category=Category.KNITWEAR, prefix="k"),
    ]
    index = build(listings, ctx, as_of_week=week(9))
    for stratum in (ERA_STRATUM, BRAND):
        for point in index.points(stratum):
            assert point.level_usd is None
            assert point.n_excluded == 0


def test_fr4_parent_is_chain_linked_from_child_log_changes(ctx):
    def outer(w: int) -> float:
        return 1000.0 * (1.20 if w >= 5 else 1.0)

    listings = [
        *flat_market(weeks=12, per_week=6, level=outer, prefix="o"),
        *flat_market(weeks=12, per_week=9, level=400.0, category=Category.KNITWEAR, prefix="k"),
    ]
    index = build(listings, ctx, as_of_week=week(11))
    parent = {p.week: p for p in index.points(ERA_STRATUM)}
    children = {stratum: {p.week: p for p in index.points(stratum)} for stratum in (LEAF, KNITWEAR)}
    weeks = sorted(parent)
    assert parent[weeks[0]].index_value == pytest.approx(100.0)
    for previous, current in pairwise(weeks):
        numerator = 0.0
        denominator = 0.0
        for stratum, points in children.items():
            weight = index.leaf_weight(stratum, current)
            numerator += weight * (
                math.log(points[current].index_value) - math.log(points[previous].index_value)
            )
            denominator += weight
        expected = math.log(parent[previous].index_value) + numerator / denominator
        assert math.log(parent[current].index_value) == pytest.approx(expected)


def test_fr4_parent_n_sales_sums_the_contributing_children(ctx):
    listings = [
        *flat_market(weeks=8, per_week=6, prefix="o"),
        *flat_market(weeks=8, per_week=7, level=400.0, category=Category.KNITWEAR, prefix="k"),
    ]
    index = build(listings, ctx, as_of_week=week(7))
    parent = index.point_at(ERA_STRATUM, week(7))
    leaves = sum(index.point_at(s, week(7)).n_sales for s in (LEAF, KNITWEAR))
    assert parent.n_sales == leaves


def test_t4_fr4_child_becoming_eligible_produces_no_step_in_the_parent(ctx):
    """EVALS T4: a first-appearing child contributes only from its *second* point."""
    listings = [
        *flat_market(weeks=20, per_week=6, level=1000.0, prefix="o"),
        *flat_market(
            weeks=10,
            per_week=6,
            level=5000.0,
            category=Category.KNITWEAR,
            prefix="k",
            start=week(10),
        ),
    ]
    index = build(listings, ctx, as_of_week=week(19))
    parent_points = index.points(ERA_STRATUM)
    assert len(parent_points) == 20
    assert index.point_at(KNITWEAR, week(10)) is not None
    for point in parent_points:
        assert point.index_value == pytest.approx(100.0), point.week


def test_fr4_brand_parent_chains_its_era_strata(ctx):
    listings = [
        *flat_market(weeks=10, per_week=6, prefix="o"),
        *flat_market(
            weeks=10,
            per_week=6,
            level=800.0,
            era_id="helmut-lang:post",
            prefix="p",
        ),
    ]
    index = build(listings, ctx, as_of_week=week(9))
    assert index.points(f"{BRAND}/post")
    brand_points = index.points(BRAND)
    assert len(brand_points) == 10
    assert brand_points[0].index_value == pytest.approx(100.0)


def test_fr4_build_is_deterministic_and_idempotent(ctx):
    listings = flat_market(weeks=15, per_week=6, noise=0.2, seed=3)
    first = build(listings, ctx, as_of_week=week(14))
    second = build(list(reversed(listings)), ctx, as_of_week=week(14))
    assert [p.model_dump() for p in first.all_points()] == [
        p.model_dump() for p in second.all_points()
    ]


def test_fr4_restrict_equals_a_build_that_only_saw_earlier_listings(ctx):
    listings = flat_market(weeks=20, per_week=6, noise=0.2, seed=5)
    full = build(listings, ctx, as_of_week=week(19))
    for cut in (week(6), week(11), week(17)):
        stamp = f"{cut}T23:59:00Z"
        rebuilt = build_index(
            listings, mapper=ctx.mapper, config=ctx.index_config, as_of=stamp, built_as_of=stamp
        )
        sliced = full.restrict(cut)
        assert [(p.stratum_id, p.week, round(p.index_value, 10)) for p in sliced.all_points()] == [
            (p.stratum_id, p.week, round(p.index_value, 10)) for p in rebuilt.all_points()
        ]


def test_fr4_weights_use_the_trailing_eight_week_survivor_count(ctx):
    listings = flat_market(weeks=12, per_week=5)
    index = build(listings, ctx, as_of_week=week(11))
    assert index.leaf_weight(LEAF, week(0)) == 5
    assert index.leaf_weight(LEAF, week(3)) == 20
    assert index.leaf_weight(LEAF, week(11)) == 40
    assert index.subtree_weight(ERA_STRATUM, week(11)) == 40
