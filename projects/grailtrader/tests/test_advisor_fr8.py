"""FR-8: the advisor — stratum choice, scope dilution, r-hat, z, confidence, action."""

from __future__ import annotations

import math

import pytest
from grailtrader.engine.advisor import (
    advise_garment,
    build_advice,
    q_index_factor,
    scope_weight,
    weekly_volatility,
)
from grailtrader.models import (
    AdviceAction,
    EventSource,
    GarmentStatus,
    HoldReason,
    ImpactDirection,
)
from grailtrader_testkit import (
    BRAND,
    ERA,
    ERA_STRATUM,
    LEAF,
    alternating_series,
    build,
    cosign,
    departure,
    flat_market,
    garment,
    index_view,
    scandal,
    week,
)

SIGMA = 1.4826 * 0.02  # the alternating-series volatility, exactly


def flat_index(weeks: int = 60, stratum: str = LEAF, level: float = 100.0):
    return index_view({stratum: alternating_series(stratum, weeks, level=level)})


def test_fr8_worked_example_is_reproducible_by_hand(ctx):
    """B == O by construction, so r-hat is exactly exp(sum m_e) - 1."""
    index = flat_index()
    event = departure(occurred_on=week(40), reason="resignation")
    decision = advise_garment(
        garment(price=1000.0, anchor_date=week(2)),
        as_of_week=week(45),
        index=index,
        events=[event],
        ctx=ctx,
    )
    assert decision.baseline_index == pytest.approx(decision.observed_index)
    assert decision.sigma_w == pytest.approx(SIGMA)

    m4 = math.log(1.12 + 0.10 * 0.5 ** (9 / 8))
    assert decision.horizon_weeks == 4
    assert decision.expected_return == pytest.approx(math.expm1(m4))
    z4 = m4 / (SIGMA * 2.0)
    assert decision.z_score == pytest.approx(z4)
    expected_conf = (1 - 0.5 ** (z4 / 0.5)) * 1.0 * 0.72
    assert decision.confidence == pytest.approx(expected_conf)
    assert decision.action is AdviceAction.BUY
    assert decision.is_candidate


def test_fr8_horizon_is_the_argmax_of_z_with_ties_to_the_smallest(ctx):
    index = flat_index()
    decision = advise_garment(
        garment(),
        as_of_week=week(45),
        index=index,
        events=[departure(occurred_on=week(40))],
        ctx=ctx,
    )
    assert decision.horizon_weeks == 4
    # a long-lived scandal keeps more of its move at 26 weeks than a 4-week decay does
    slow = advise_garment(
        garment(),
        as_of_week=week(41),
        index=index,
        events=[scandal(occurred_on=week(40), severity="severe")],
        ctx=ctx,
    )
    assert slow.horizon_weeks in {4, 12, 26}
    assert slow.action is AdviceAction.SELL


def test_fr8_sell_into_decay_is_reachable_when_the_index_has_already_run(ctx):
    """The modelled forward path falls below an observed level that already spiked."""
    weeks = [(week(i), 100.0 * math.exp(0.02 * (i % 2))) for i in range(40)]
    weeks += [(week(i), 150.0 * math.exp(0.02 * (i % 2))) for i in range(40, 62)]
    index = index_view({LEAF: weeks})
    decision = advise_garment(
        garment(),
        as_of_week=week(60),
        index=index,
        events=[departure(occurred_on=week(40), reason="death")],
        ctx=ctx,
    )
    assert decision.action is AdviceAction.SELL
    assert all(d.direction is ImpactDirection.BULLISH for d in decision.drivers)
    assert decision.expected_return <= -ctx.settings.theta_sell


def test_fr8_scope_dilution_applies_the_event_to_only_its_share_of_the_parent(ctx):
    index = index_view(
        {BRAND: alternating_series(BRAND, 60)},
        weights={
            f"{BRAND}/helmut/outerwear": {week(i): 30 for i in range(60)},
            f"{BRAND}/post/outerwear": {week(i): 70 for i in range(60)},
        },
    )
    decision = advise_garment(
        garment(),
        as_of_week=week(45),
        index=index,
        events=[departure(occurred_on=week(40))],
        ctx=ctx,
    )
    assert decision.stratum_id == BRAND
    assert decision.drivers[0].lam == pytest.approx(0.30)
    m4 = math.log(1.12 + 0.10 * 0.5 ** (9 / 8))
    assert decision.expected_return == pytest.approx(math.expm1(0.30 * m4))
    assert "mod:lambda=0.30@" + decision.drivers[0].event_id in decision.rationale_codes


def test_fr8_lambda_is_one_when_the_stratum_sits_at_or_below_the_target(ctx):
    index = flat_index()
    assert scope_weight(index, stratum=LEAF, target=ERA_STRATUM, week=week(3)) == 1.0
    assert scope_weight(index, stratum=LEAF, target=BRAND, week=week(3)) == 1.0
    assert scope_weight(index, stratum=LEAF, target=LEAF, week=week(3)) == 1.0


def test_fr8_lambda_falls_back_to_equal_leaf_counts_without_recent_sales(ctx):
    index = index_view(
        {BRAND: alternating_series(BRAND, 10)},
        weights={
            f"{BRAND}/helmut/outerwear": {},
            f"{BRAND}/post/outerwear": {},
            f"{BRAND}/post/denim": {},
        },
    )
    assert scope_weight(index, stratum=BRAND, target=ERA_STRATUM, week=week(3)) == pytest.approx(
        1 / 3
    )


def test_fr8_events_are_matched_on_the_leaf_but_applied_to_the_chosen_stratum(ctx):
    index = flat_index()
    other_era = garment(era_id="helmut-lang:post")
    decision = advise_garment(
        other_era,
        as_of_week=week(45),
        index=index,
        events=[departure(occurred_on=week(40))],
        ctx=ctx,
    )
    # the departure closes helmut-lang/helmut, which is not a prefix of .../post/...
    assert decision.hold_reason is HoldReason.NO_INDEX


def test_fr8_confidence_uses_source_factor_and_corroboration(ctx):
    index = flat_index()
    news = departure(occurred_on=week(40), source=EventSource.NEWS, refs=("https://www.wwd.com/a",))
    corroborated = departure(
        occurred_on=week(40),
        source=EventSource.NEWS,
        refs=("https://www.wwd.com/a", "https://www.voguebusiness.com/b"),
    )
    lone = advise_garment(garment(), as_of_week=week(45), index=index, events=[news], ctx=ctx)
    both = advise_garment(
        garment(), as_of_week=week(45), index=index, events=[corroborated], ctx=ctx
    )
    assert lone.c_event == pytest.approx(0.72 * 0.95)
    assert both.c_event == pytest.approx(0.72 * min(1.0, 0.95 * 1.05))
    assert both.confidence > lone.confidence


def test_fr8_confidence_can_never_exceed_the_strongest_prior_base_conf(ctx):
    index = flat_index()
    decision = advise_garment(
        garment(),
        as_of_week=week(40),
        index=index,
        events=[departure(occurred_on=week(40), reason="death")],
        ctx=ctx,
    )
    assert decision.confidence <= 0.78
    assert decision.confidence <= ctx.settings.conf_cap


def test_fr8_q_index_damps_a_stale_stratum(ctx):
    settings = ctx.settings
    assert q_index_factor(0, settings) == 1.0
    assert q_index_factor(2, settings) == 0.8
    assert q_index_factor(8, settings) == 0.5

    series = [(week(i), 100.0 * math.exp(0.02 * (i % 2))) for i in range(58)]
    index = index_view({LEAF: series})
    decision = advise_garment(
        garment(),
        as_of_week=week(60),
        index=index,
        events=[departure(occurred_on=week(40))],
        ctx=ctx,
    )
    assert decision.stratum_staleness_weeks == 3
    assert decision.q_index == 0.5


def test_fr8_volatility_is_gap_normalised_and_needs_enough_pairs(ctx):
    index = flat_index(weeks=40)
    points = index.points(LEAF)
    sigma, pairs = weekly_volatility(points, sigma_min_changes=12)
    assert pairs == 39
    assert sigma == pytest.approx(SIGMA)
    assert weekly_volatility(points[:6], sigma_min_changes=12) == (None, 5)

    gapped = [points[0], points[4]]
    sigma_gapped, _ = weekly_volatility(gapped, sigma_min_changes=1)
    expected = 1.4826 * abs(math.log(points[4].index_value) - math.log(points[0].index_value)) / 2.0
    assert sigma_gapped == pytest.approx(expected)


def test_fr8_rationale_codes_name_every_driver_and_modifier(ctx):
    index = flat_index()
    event = departure(occurred_on=week(40))
    decision = advise_garment(garment(), as_of_week=week(45), index=index, events=[event], ctx=ctx)
    codes = decision.rationale_codes
    assert f"driver:event:{event.id}" in codes
    assert "prior:designer_departure.resignation" in codes
    assert any(code.startswith("mod:z=") and code.endswith("@h4") for code in codes)
    assert any(code.startswith("mod:conf_event=") for code in codes)
    assert "mod:q_index=1.0" in codes
    assert "mod:src=manual" in codes


def test_fr8_inputs_hash_changes_only_when_the_inputs_change(ctx):
    index = flat_index()
    first = departure(occurred_on=week(40))
    base = advise_garment(garment(), as_of_week=week(45), index=index, events=[first], ctx=ctx)
    same = advise_garment(garment(), as_of_week=week(45), index=index, events=[first], ctx=ctx)
    assert base.inputs_hash == same.inputs_hash

    extra = cosign(occurred_on=week(44), celebrity="A", era_id=ERA)
    with_new_event = advise_garment(
        garment(), as_of_week=week(45), index=index, events=[first, extra], ctx=ctx
    )
    assert with_new_event.inputs_hash != base.inputs_hash


def test_fr8_advice_identity_never_keys_on_the_horizon(ctx):
    index = flat_index()
    event = departure(occurred_on=week(40))
    piece = garment()
    first = build_advice(
        advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx),
        garment=piece,
        events_by_id={event.id: event},
        ctx=ctx,
        created_as_of="2026-01-01T00:00:00Z",
    )
    second = build_advice(
        advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx),
        garment=piece,
        events_by_id={event.id: event},
        ctx=ctx,
        created_as_of="2026-02-01T00:00:00Z",
    )
    assert first.id == second.id
    assert first.rendered_text == second.rendered_text
    assert first.horizon_weeks == second.horizon_weeks


def test_fr8_soft_deleted_and_archived_garments_leave_the_pipeline(ctx):
    index = flat_index()
    removed = garment().model_copy(update={"deleted_at": "2026-01-05T00:00:00Z"})
    with pytest.raises(ValueError, match="soft-deleted"):
        advise_garment(removed, as_of_week=week(45), index=index, events=[], ctx=ctx)
    archived = garment(status=GarmentStatus.SOLD_ARCHIVED)
    with pytest.raises(ValueError, match="archived"):
        advise_garment(archived, as_of_week=week(45), index=index, events=[], ctx=ctx)


# --------------------------------------------------------------------------- #
# T6 — every one of the seven hold reason codes is reachable                    #
# --------------------------------------------------------------------------- #


def test_t6_fr8_hold_no_index(ctx):
    decision = advise_garment(
        garment(), as_of_week=week(45), index=index_view({}), events=[], ctx=ctx
    )
    assert decision.hold_reason is HoldReason.NO_INDEX
    assert decision.rationale_codes == ("hold:no_index",)
    assert not decision.stratum_selected


def test_t6_fr8_hold_stale_index(ctx):
    index = index_view({LEAF: alternating_series(LEAF, 20)})
    decision = advise_garment(garment(), as_of_week=week(45), index=index, events=[], ctx=ctx)
    assert decision.hold_reason is HoldReason.STALE_INDEX
    assert not decision.stratum_selected


def test_t6_fr8_hold_no_active_events(ctx):
    decision = advise_garment(
        garment(), as_of_week=week(45), index=flat_index(), events=[], ctx=ctx
    )
    assert decision.hold_reason is HoldReason.NO_ACTIVE_EVENTS
    assert decision.rationale_codes == ("hold:no_active_events",)


def test_t6_fr8_hold_no_baseline(ctx):
    index = index_view({LEAF: alternating_series(LEAF, 60, start=week(10))})
    decision = advise_garment(
        garment(anchor_date=week(10)),
        as_of_week=week(20),
        index=index,
        events=[departure(occurred_on=week(10))],
        ctx=ctx,
    )
    assert decision.hold_reason is HoldReason.NO_BASELINE


def test_t6_fr8_hold_insufficient_history(ctx):
    index = index_view({LEAF: alternating_series(LEAF, 8)})
    decision = advise_garment(
        garment(),
        as_of_week=week(7),
        index=index,
        events=[departure(occurred_on=week(3))],
        ctx=ctx,
    )
    assert decision.hold_reason is HoldReason.INSUFFICIENT_HISTORY
    assert decision.confidence is None


def test_t6_fr8_hold_below_threshold(ctx):
    decision = advise_garment(
        garment(),
        as_of_week=week(45),
        index=flat_index(),
        events=[cosign(occurred_on=week(45), celebrity="A", tier="niche", era_id=ERA)],
        ctx=ctx,
    )
    assert decision.hold_reason is HoldReason.BELOW_THRESHOLD
    assert not decision.is_candidate
    assert abs(decision.expected_return) < ctx.settings.theta_buy


def test_t6_fr8_hold_low_confidence(ctx):
    # Four stacked a-list co-signs clear theta together, but base_conf 0.50 keeps
    # confidence below conf_min: co-signs are context, never a trade (FR-8).
    events = [
        cosign(occurred_on=week(45), celebrity=name, tier="a_list", era_id=ERA)
        for name in ("A", "B", "C", "D")
    ]
    decision = advise_garment(
        garment(), as_of_week=week(45), index=flat_index(), events=events, ctx=ctx
    )
    assert decision.hold_reason is HoldReason.LOW_CONFIDENCE
    assert decision.is_candidate
    assert decision.expected_return >= ctx.settings.theta_buy
    assert decision.confidence < ctx.settings.conf_min


def test_fr8_holds_carry_exactly_one_hold_code(ctx):
    index = flat_index()
    decision = advise_garment(
        garment(),
        as_of_week=week(45),
        index=index,
        events=[cosign(occurred_on=week(45), celebrity="A", tier="niche", era_id=ERA)],
        ctx=ctx,
    )
    assert len([c for c in decision.rationale_codes if c.startswith("hold:")]) == 1


def test_fr8_thresholds_come_from_config_not_code(ctx):
    """Raising theta turns the same decision into a hold — no constant is hard-coded."""
    index = flat_index()
    event = departure(occurred_on=week(40))
    piece = garment()
    assert (
        advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx).action
        is AdviceAction.BUY
    )

    settings = ctx.settings.model_copy(
        update={"theta_buy": 0.5, "theta_sell": 0.5, "fee_assumption_pct": 0.5}
    )
    config = ctx.config.model_copy(update={"advisor": settings})
    strict = ctx.__class__(
        gazetteer=ctx.gazetteer,
        mapper=ctx.mapper,
        priors=ctx.priors,
        config=config,
        templates=ctx.templates,
    )
    decision = advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=strict)
    assert decision.action is AdviceAction.HOLD
    assert decision.hold_reason is HoldReason.BELOW_THRESHOLD


def test_fr8_runs_on_a_recovered_index_end_to_end(ctx):
    listings = flat_market(weeks=60, per_week=8, noise=0.18, seed=21)
    index = build(listings, ctx, as_of_week=week(59))
    decision = advise_garment(
        garment(anchor_date=week(2)),
        as_of_week=week(55),
        index=index,
        events=[departure(occurred_on=week(52), reason="death")],
        ctx=ctx,
    )
    assert decision.stratum_id == LEAF
    assert decision.action in {AdviceAction.BUY, AdviceAction.HOLD}
    assert decision.sigma_w is not None and decision.sigma_w > 0
    assert decision.fair_value is not None


def test_fr8_reproduces_the_data_model_worked_example(ctx):
    """DATA_MODEL's advice arithmetic, pinned end to end.

    Planted so that B = 120.0 (last point before the event week), O_t = 125.0 and
    sigma_w = 0.035 exactly; the garment is anchored at an index of 118.0. The
    expected values are DATA_MODEL's own: A_e = 26, r-hat = 0.12320 at H* = 4,
    z = 1.6597, conf = 0.6479 -> buy, fair value 1,309.32.
    """
    step = 0.035 / 1.4826  # so sigma_w = 1.4826 * median|delta| = 0.035
    series = []
    for offset in range(46):
        if offset < 41:
            value = 120.0 * math.exp(step) if offset % 2 else 120.0
        else:
            value = 125.0 if offset % 2 else 125.0 * math.exp(step)
        series.append((week(offset), value))
    series[5] = (week(5), 118.0)  # the garment's anchor week
    index = index_view({LEAF: series})

    piece = garment(price=1236.00, anchor_date=week(5))
    event = departure(occurred_on=week(41), reason="resignation")
    decision = advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx)

    assert decision.baseline_index == pytest.approx(120.0)
    assert decision.observed_index == pytest.approx(125.0)
    assert decision.sigma_w == pytest.approx(0.035, abs=1e-9)
    assert decision.drivers[0].age_weeks == 4
    assert decision.drivers[0].retirement_age_weeks == pytest.approx(26.0)
    assert decision.horizon_weeks == 4
    assert decision.expected_return == pytest.approx(0.12320, abs=5e-6)
    assert decision.z_score == pytest.approx(1.6597, abs=5e-5)
    assert decision.c_event == pytest.approx(0.72)
    assert decision.q_index == 1.0
    assert decision.confidence == pytest.approx(0.6479, abs=5e-5)
    assert decision.action is AdviceAction.BUY
    assert decision.fair_value == pytest.approx(1309.32, abs=0.01)
    assert decision.fair_value_method.value == "repeat_sales"
