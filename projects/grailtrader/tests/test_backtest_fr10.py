"""FR-10: the backtest harness, its leak canaries (T3), baselines and placebo mode."""

from __future__ import annotations

import math

import pytest
from grailtrader.engine.advisor import advise_garment
from grailtrader.engine.backtest import ReferenceIndex, displace_events, run_backtest
from grailtrader.engine.index import build_index
from grailtrader.models import BacktestParams, EventStatus, ExclusionReason
from grailtrader.weeks import add_weeks, weeks_between
from grailtrader_testkit import (
    BRAND,
    LEAF,
    build,
    departure,
    flat_market,
    garment,
    scandal,
    sold_listing,
    week,
)

WEEKS = 80
EVENT_WEEK = 30


def scenario_listings():
    def level(w: int) -> float:
        if w < EVENT_WEEK + 2:
            return 1000.0
        if w < EVENT_WEEK + 4:
            return 1100.0
        return 1200.0

    return flat_market(weeks=WEEKS, per_week=8, level=level, noise=0.12, seed=17)


def truth_reference() -> ReferenceIndex:
    def level(w: int) -> float:
        if w < EVENT_WEEK + 2:
            return 100.0
        if w < EVENT_WEEK + 4:
            return 110.0
        return 120.0

    series = {LEAF: {week(w): level(w) for w in range(WEEKS + 40)}}
    series[f"{BRAND}/helmut"] = dict(series[LEAF])
    series[BRAND] = dict(series[LEAF])
    return ReferenceIndex.from_truth(series)


def run(ctx, *, placebo_seed=None, start=20, end=WEEKS - 1, events=None, garments=None):
    listings = scenario_listings()
    index = build(listings, ctx, as_of_week=week(WEEKS - 1))
    params = BacktestParams(
        start_week=week(start),
        end_week=week(end),
        placebo_seed=placebo_seed,
        reference="truth",
        scenario="unit",
    )
    return run_backtest(
        params=params,
        garments=garments if garments is not None else [garment(anchor_date=week(2))],
        events=events if events is not None else [departure(occurred_on=week(EVENT_WEEK))],
        index=index,
        reference=truth_reference(),
        ctx=ctx,
        as_of="2026-01-01T00:00:00Z",
    )


def test_fr10_entry_is_next_week(ctx):
    """T3 canary: entry is week t+1, asserted for every decision."""
    _run, results = run(ctx)
    assert results
    for result in results:
        assert result.entry_week == add_weeks(result.week, 1)
        assert weeks_between(result.week, result.entry_week) == 1


def test_fr10_advice_ignores_future_listings(ctx):
    """T3 canary: a sale in week t+1 that would flip the index must not reach week t."""
    listings = scenario_listings()
    spike = [
        sold_listing(external_id=f"spike-{i}", price=25000.0, week_key=week(41)) for i in range(40)
    ]
    clean = build(listings, ctx, as_of_week=week(WEEKS - 1))
    polluted = build([*listings, *spike], ctx, as_of_week=week(WEEKS - 1))
    assert (
        polluted.point_at(LEAF, week(41)).level_usd > 2 * clean.point_at(LEAF, week(41)).level_usd
    )

    piece = garment(anchor_date=week(2))
    events = [departure(occurred_on=week(EVENT_WEEK))]
    before = advise_garment(
        piece, as_of_week=week(40), index=clean.restrict(week(40)), events=events, ctx=ctx
    )
    after = advise_garment(
        piece, as_of_week=week(40), index=polluted.restrict(week(40)), events=events, ctx=ctx
    )
    assert before.model_dump() == after.model_dump()


def test_fr10_prefix_index_equivalence(ctx):
    """T3: the index built from listings sold through week t == the full build sliced at t."""
    listings = scenario_listings()
    full = build(listings, ctx, as_of_week=week(WEEKS - 1))
    for cut in (week(25), week(40), week(63)):
        stamp = f"{cut}T23:59:59Z"
        partial = build_index(
            listings, mapper=ctx.mapper, config=ctx.index_config, as_of=stamp, built_as_of=stamp
        )
        sliced = full.restrict(cut)
        assert [
            (p.stratum_id, p.week, round(p.index_value, 12), p.n_sales) for p in sliced.all_points()
        ] == [
            (p.stratum_id, p.week, round(p.index_value, 12), p.n_sales)
            for p in partial.all_points()
        ]


def test_fr10_replay_uses_only_strictly_prior_events(ctx):
    _run, results = run(ctx)
    by_week = {r.week: r for r in results}
    early = by_week[week(EVENT_WEEK - 1)]
    assert early.driver_event_ids == ()
    later = by_week[week(EVENT_WEEK + 1)]
    assert later.driver_event_ids


def test_fr10_out_of_window_is_reported_but_excluded_from_every_rate(ctx):
    run_row, results = run(ctx)
    excluded = run_row.aggregates["excluded"]
    assert excluded[ExclusionReason.OUT_OF_WINDOW.value] > 0
    out = [r for r in results if r.excluded_reason is ExclusionReason.OUT_OF_WINDOW]
    for result in out:
        assert result.realized_return is None
        assert result.hit is None
        assert add_weeks(result.entry_week, result.horizon_weeks) > run_row.params.end_week
    graded = [r for r in results if r.realized_return is not None]
    assert run_row.aggregates["n_candidates"] == len([r for r in graded if r.is_candidate])


def test_fr10_hit_is_computed_for_every_graded_candidate(ctx):
    _run, results = run(ctx)
    for result in results:
        if result.is_candidate and result.realized_return is not None:
            assert result.hit is not None
            assert result.hit == ((result.realized_return > 0) == (result.expected_return > 0))
        else:
            assert result.hit is None


def test_fr10_aggregates_have_the_documented_shape(ctx):
    run_row, _results = run(ctx)
    aggregates = run_row.aggregates
    for key in (
        "n_candidates",
        "n_actionable",
        "excluded",
        "hit_rate",
        "mean_realized",
        "spread_buy_minus_sell",
        "spread_by_horizon",
        "buckets",
        "by_event_type",
        "regimes",
        "baselines",
    ):
        assert key in aggregates
    assert set(aggregates["buckets"]) == {"lo", "mid", "hi"}
    assert set(aggregates["excluded"]) == {reason.value for reason in ExclusionReason}
    assert set(aggregates["baselines"]) == {"always_hold", "theta_momentum", "event_naive"}
    assert set(aggregates["spread_by_horizon"]) == {"4", "12", "26"}
    assert aggregates["baselines"]["always_hold"]["hit_rate"] is None
    assert aggregates["baselines"]["always_hold"]["spread"] == 0.0


def test_fr10_a_planted_move_produces_directional_skill(ctx):
    run_row, results = run(ctx)
    assert run_row.aggregates["n_actionable"] >= 3
    assert run_row.aggregates["hit_rate"] >= 0.7
    assert run_row.aggregates["regimes"]["phase_in_buy"] >= 1
    assert any(r.action.value == "buy" for r in results)


def test_fr10_run_id_is_derived_from_the_params(ctx):
    first, _ = run(ctx)
    second, _ = run(ctx)
    other, _ = run(ctx, start=21)
    assert first.id == second.id
    assert first.id != other.id


def test_fr10_replay_is_byte_identical_across_runs(ctx):
    first_run, first_results = run(ctx)
    second_run, second_results = run(ctx)
    assert first_run.model_dump() == second_run.model_dump()
    assert [r.model_dump() for r in first_results] == [r.model_dump() for r in second_results]


def test_fr10_pending_events_never_reach_the_replay(ctx):
    pending = departure(occurred_on=week(EVENT_WEEK), status=EventStatus.PENDING)
    run_row, results = run(ctx, events=[pending])
    assert all(r.driver_event_ids == () for r in results)
    assert run_row.aggregates["n_candidates"] == 0


def test_fr10_missing_index_is_recorded_as_an_exclusion(ctx):
    piece = garment(brand_id="rick-owens", era_id="rick-owens:rick")
    run_row, results = run(ctx, garments=[piece], events=[])
    assert all(r.excluded_reason is ExclusionReason.NO_INDEX for r in results)
    assert run_row.aggregates["excluded"][ExclusionReason.NO_INDEX.value] == len(results)
    assert run_row.aggregates["n_candidates"] == 0


def test_fr10_placebo_displaces_every_event_away_from_true_impact_windows(ctx):
    events = [
        departure(occurred_on=week(EVENT_WEEK)),
        scandal(occurred_on=week(50), severity="moderate"),
    ]
    displaced = displace_events(events, seed=20260731, ctx=ctx)
    assert len(displaced) == len(events)
    for original, moved in zip(sorted(events, key=lambda e: e.week), displaced, strict=True):
        offset = abs(weeks_between(original.week, moved.week))
        assert 26 <= offset <= 52
        assert moved.id != original.id


def test_fr10_placebo_is_seeded_and_reproducible(ctx):
    events = [
        departure(occurred_on=week(EVENT_WEEK)),
        scandal(occurred_on=week(50), severity="moderate"),
        scandal(occurred_on=week(62), severity="minor", brand_id="celine"),
    ]
    first = displace_events(events, seed=20260731, ctx=ctx)
    again = displace_events(events, seed=20260731, ctx=ctx)
    other = displace_events(events, seed=20260802, ctx=ctx)
    assert [e.occurred_on for e in first] == [e.occurred_on for e in again]
    assert [e.occurred_on for e in first] != [e.occurred_on for e in other]


def test_fr10_placebo_runs_never_mix_into_the_real_aggregates(ctx):
    real, _ = run(ctx)
    placebo, _ = run(ctx, placebo_seed=20260731)
    assert real.id != placebo.id
    assert placebo.params.placebo_seed == 20260731
    assert real.params.placebo_seed is None


def test_fr10_recovered_reference_carries_back_within_the_stale_window(ctx):
    listings = flat_market(weeks=20, per_week=6)
    index = build(listings, ctx, as_of_week=week(19))
    reference = ReferenceIndex.from_index(index, carry_back_weeks=8)
    assert reference.value(LEAF, week(5)) == pytest.approx(100.0)
    assert reference.value(LEAF, week(60)) is None
    exact = ReferenceIndex.from_truth({LEAF: {week(5): 123.0}})
    assert exact.value(LEAF, week(5)) == 123.0
    assert exact.value(LEAF, week(6)) is None


def test_fr10_realized_return_is_measured_at_the_advice_horizon(ctx):
    _run_row, results = run(ctx)
    reference = truth_reference()
    for result in results:
        if result.realized_return is None:
            continue
        start = reference.value(LEAF, result.entry_week)
        finish = reference.value(LEAF, add_weeks(result.entry_week, result.horizon_weeks))
        assert result.realized_return == pytest.approx(finish / start - 1.0)
        assert not math.isnan(result.realized_return)
