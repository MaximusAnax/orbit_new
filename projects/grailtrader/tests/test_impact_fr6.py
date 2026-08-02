"""FR-6: the permanent-plus-decaying-transient impact model and bounded retirement."""

from __future__ import annotations

import math

import pytest
from grailtrader.engine.events import make_event
from grailtrader.engine.impact import (
    active_legs,
    combined_log_impact,
    legs_for_event,
    prior_key_for,
    reachable_prior_keys,
    retirement_age,
)
from grailtrader.models import EventSource, EventStatus, EventType, TargetKind
from grailtrader_testkit import BRAND, ERA, LEAF, START, cosign, departure, scandal, week


def leg_for(ctx, event, index: int = 0):
    return legs_for_event(event, ctx.gazetteer, ctx.priors)[index]


def test_fr6_m_e_is_the_adstock_path(ctx):
    leg = leg_for(ctx, departure(occurred_on=START, reason="resignation"))
    assert leg.permanent == pytest.approx(0.12)
    assert leg.transient == pytest.approx(0.10)
    assert leg.half_life == pytest.approx(8.0)
    assert leg.m_at(0) == pytest.approx(math.log(1.22))
    assert leg.m_at(8) == pytest.approx(math.log(1.17))
    assert leg.m_at(16) == pytest.approx(math.log(1.145))
    assert leg.m_at(30) == pytest.approx(math.log(1 + 0.12 + 0.10 * 0.5 ** (30 / 8)))


def test_fr6_m_e_decays_towards_the_permanent_component(ctx):
    leg = leg_for(ctx, departure(occurred_on=START, reason="death"))
    assert leg.m_at(0) > leg.m_at(6) > leg.m_at(52) > math.log(1.20) - 1e-6
    assert leg.m_at(400) == pytest.approx(math.log(1.20), abs=1e-9)


def test_fr6_bearish_priors_produce_negative_impacts(ctx):
    leg = leg_for(ctx, scandal(occurred_on=START, severity="severe"))
    assert leg.m_at(0) == pytest.approx(math.log(0.70))
    assert leg.m_at(0) < 0


def test_fr6_negative_age_is_not_a_valid_path_point(ctx):
    leg = leg_for(ctx, departure(occurred_on=START))
    with pytest.raises(ValueError, match="non-negative"):
        leg.m_at(-1)


def test_fr6_cosign_transient_scales_by_tier(ctx):
    tiers = {
        tier: leg_for(ctx, cosign(occurred_on=START, celebrity=f"C{tier}", tier=tier)).transient
        for tier in ("a_list", "b_list", "niche")
    }
    assert tiers["a_list"] == pytest.approx(0.10)
    assert tiers["b_list"] == pytest.approx(0.05)
    assert tiers["niche"] == pytest.approx(0.025)


def test_fr6_overlapping_events_combine_log_additively(ctx):
    events = [
        departure(occurred_on=START),
        cosign(occurred_on=START, celebrity="A", era_id=ERA),
    ]
    legs = active_legs(
        events,
        leaf=LEAF,
        as_of_week=week(2),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    assert len(legs) == 2
    total = combined_log_impact(legs, as_of_week=week(2))
    assert total == pytest.approx(sum(leg.m_at(2) for leg in legs))


def test_fr6_retirement_is_conjunctive_and_bounded(ctx):
    settings = ctx.settings
    resignation = leg_for(ctx, departure(occurred_on=START, reason="resignation"))
    assert retirement_age(resignation, settings) == pytest.approx(26.0)

    collab = leg_for(
        ctx,
        make_event(
            event_type=EventType.COLLAB_ANNOUNCEMENT,
            brand_id=BRAND,
            occurred_on=START,
            source=EventSource.NEWS,
            source_refs=("https://hypebeast.com/x",),
            status=EventStatus.CONFIRMED,
            attributes={"counterparty": "Someone"},
        ),
    )
    assert retirement_age(collab, settings) == pytest.approx(4.0 * math.log2(0.10 / 0.005))

    niche = leg_for(ctx, cosign(occurred_on=START, celebrity="N", tier="niche"))
    assert retirement_age(niche, settings) == pytest.approx(3.0 * math.log2(0.025 / 0.005))


def test_fr6_a_permanent_only_prior_retires_at_the_max_age(ctx):
    """A zero/near-zero transient must not keep an event active forever (REVIEW D2)."""
    leg = leg_for(ctx, departure(occurred_on=START))
    tiny = leg.__class__(**{**leg.__dict__, "transient": 0.0})
    assert retirement_age(tiny, ctx.settings) == float(ctx.settings.active_max_weeks)


def test_fr6_events_retire_so_a_quiet_week_is_reachable(ctx):
    event = departure(occurred_on=START)
    inside = active_legs(
        [event],
        leaf=LEAF,
        as_of_week=week(26),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    outside = active_legs(
        [event],
        leaf=LEAF,
        as_of_week=week(27),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    assert len(inside) == 1
    assert outside == ()


def test_fr6_events_are_inactive_before_they_happen(ctx):
    event = departure(occurred_on=week(10))
    assert (
        active_legs(
            [event],
            leaf=LEAF,
            as_of_week=week(9),
            gazetteer=ctx.gazetteer,
            priors=ctx.priors,
            settings=ctx.settings,
        )
        == ()
    )


def test_fr6_forward_path_is_evaluated_beyond_the_retirement_age(ctx):
    """An active event's contribution at t+H uses the future age, even past A_e."""
    event = departure(occurred_on=START)
    legs = active_legs(
        [event],
        leaf=LEAF,
        as_of_week=week(20),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    forward = combined_log_impact(legs, as_of_week=week(20), horizon_weeks=26)
    assert forward == pytest.approx(legs[0].m_at(46))
    assert forward < legs[0].m_at(20)


def test_fr6_scope_weights_dilute_the_combination(ctx):
    event = departure(occurred_on=START)
    legs = active_legs(
        [event],
        leaf=LEAF,
        as_of_week=week(2),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    key = (legs[0].event_id, legs[0].target.stratum)
    diluted = combined_log_impact(legs, as_of_week=week(2), scope_weights={key: 0.25})
    assert diluted == pytest.approx(0.25 * legs[0].m_at(2))


@pytest.mark.parametrize(
    ("event_kind", "kind", "expected"),
    [
        ("departure", TargetKind.ERA, "designer_departure.resignation"),
        ("scandal", TargetKind.BRAND, "brand_scandal.severe"),
        ("cosign", TargetKind.GIVEN, "celebrity_cosign.*"),
    ],
)
def test_fr6_prior_keys_follow_the_typology(ctx, event_kind, kind, expected):
    event = {
        "departure": departure(occurred_on=START),
        "scandal": scandal(occurred_on=START),
        "cosign": cosign(occurred_on=START, celebrity="A"),
    }[event_kind]
    assert prior_key_for(event, kind) == expected


def test_fr6_appointment_produces_two_legs_with_different_priors(ctx):
    event = make_event(
        event_type=EventType.DESIGNER_APPOINTMENT,
        brand_id="celine",
        occurred_on="2018-02-05",
        source=EventSource.NEWS,
        source_refs=("https://www.wwd.com/x",),
        status=EventStatus.CONFIRMED,
        attributes={"designer": "Hedi Slimane", "acclaim": "acclaimed"},
    )
    legs = legs_for_event(event, ctx.gazetteer, ctx.priors)
    assert [leg.prior_key for leg in legs] == [
        "designer_appointment.acclaimed.brand",
        "designer_appointment.*.predecessor_era",
    ]
    assert [leg.base_conf for leg in legs] == [0.62, 0.64]


def test_fr6_every_reachable_prior_key_resolves(ctx):
    for key in reachable_prior_keys():
        assert key in ctx.priors


def test_fr6_engine_hard_codes_no_expected_move(ctx):
    """Priors are editable data: change the file, change the modelled move."""
    prior = ctx.priors["designer_departure.resignation"]
    edited = prior.model_copy(update={"permanent_pct": 0.30})
    priors = {**ctx.priors, "designer_departure.resignation": edited}
    legs = active_legs(
        [departure(occurred_on=START)],
        leaf=LEAF,
        as_of_week=week(1),
        gazetteer=ctx.gazetteer,
        priors=priors,
        settings=ctx.settings,
    )
    assert legs[0].m_at(0) == pytest.approx(math.log(1.40))
