"""FR-5: event identity, cross-feed deduplication and scope resolution."""

from __future__ import annotations

import pytest
from grailtrader.engine.events import (
    applies_to,
    corroboration_of,
    ingest_events,
    make_event,
    merge_event,
    registrable_domain,
    target_strata,
    transition_status,
)
from grailtrader.engine.impact import active_legs, combined_log_impact
from grailtrader.models import (
    EventSource,
    EventStatus,
    EventType,
    FashionEvent,
    TargetKind,
)
from grailtrader_testkit import BRAND, ERA, START, cosign, departure, scandal, week
from pydantic import ValidationError


def test_fr5_identity_ignores_judgement_attributes_and_source_ref():
    resigned = departure(occurred_on=START, reason="resignation", refs=("https://a.example.com/1",))
    ousted = departure(occurred_on=START, reason="ousted", refs=("https://b.example.org/2",))
    assert resigned.id == ousted.id


def test_fr5_identity_keys_on_the_week_not_the_exact_day():
    monday = departure(occurred_on=START)
    thursday = departure(occurred_on="2024-01-04")
    next_week = departure(occurred_on=week(1))
    assert monday.id == thursday.id
    assert monday.id != next_week.id


def test_fr5_identity_includes_factual_discriminators():
    a = cosign(occurred_on=START, celebrity="Person A")
    b = cosign(occurred_on=START, celebrity="Person B")
    assert a.id != b.id
    assert a.identity_attrs == {"celebrity": "Person A"}


def test_fr5_departure_identity_attrs_are_empty():
    assert departure(occurred_on=START).identity_attrs == {}


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("manual:vantorre-departure", "manual"),
        ("https://www.businessoffashion.com/articles/x", "businessoffashion.com"),
        ("https://feeds.vogue.co.uk/news/1", "vogue.co.uk"),
        ("http://hypebeast.com/2024/1", "hypebeast.com"),
        ("social:some-handle", "social"),
    ],
)
def test_fr5_registrable_domain(ref, expected):
    assert registrable_domain(ref) == expected


def test_fr5_corroboration_counts_distinct_domains():
    assert corroboration_of(["manual:a", "manual:b"]) == 1
    assert (
        corroboration_of(
            ["https://www.wwd.com/a", "https://wwd.com/b", "https://voguebusiness.com/c"]
        )
        == 2
    )


def test_fr5_second_feed_bumps_corroboration_without_a_second_row():
    first = departure(occurred_on=START, source=EventSource.NEWS, refs=("https://www.wwd.com/a",))
    second = departure(
        occurred_on="2024-01-03",
        reason="ousted",
        source=EventSource.NEWS,
        refs=("https://www.voguebusiness.com/b",),
    )
    merged = merge_event(first, second)
    assert merged.id == first.id
    assert merged.corroboration == 2
    assert merged.source_refs == ("https://www.wwd.com/a", "https://www.voguebusiness.com/b")
    # first-seen wins on the judgement attribute and on occurred_on
    assert merged.attributes["reason"] == "resignation"
    assert merged.occurred_on == START


def test_fr5_reingesting_the_same_feed_is_a_no_op():
    events = [departure(occurred_on=START), scandal(occurred_on=week(2))]
    store, report = ingest_events([], events)
    assert report.created == 2
    again, report2 = ingest_events(store.values(), events)
    assert report2.created == 0
    assert report2.corroborated == 0
    assert {k: v.model_dump() for k, v in again.items()} == {
        k: v.model_dump() for k, v in store.items()
    }


def test_t5_fr5_duplicate_feed_leaves_sum_of_impacts_unchanged(ctx):
    """EVALS T5: corroboration rises, `sum_e m_e` on every target stratum does not."""
    leaf = f"{BRAND}/helmut/outerwear"
    first = departure(occurred_on=START, source=EventSource.NEWS, refs=("https://www.wwd.com/a",))
    store, _ = ingest_events([], [first])

    def total(events):
        legs = active_legs(
            events,
            leaf=leaf,
            as_of_week=week(3),
            gazetteer=ctx.gazetteer,
            priors=ctx.priors,
            settings=ctx.settings,
        )
        return combined_log_impact(legs, as_of_week=week(3))

    before = total(store.values())
    duplicate = departure(
        occurred_on=START,
        reason="ousted",
        source=EventSource.NEWS,
        refs=("https://www.voguebusiness.com/b",),
    )
    store2, report = ingest_events(store.values(), [duplicate])
    assert len(store2) == 1
    assert report.corroborated == 1
    (event,) = store2.values()
    assert event.corroboration == 2
    assert total(store2.values()) == pytest.approx(before)


def test_fr5_departure_targets_the_closing_era(ctx):
    targets = target_strata(departure(occurred_on=START), ctx.gazetteer)
    assert [(t.kind, t.stratum) for t in targets] == [(TargetKind.ERA, "helmut-lang/helmut")]


def test_fr5_appointment_targets_the_brand_and_the_predecessor_era(ctx):
    event = make_event(
        event_type=EventType.DESIGNER_APPOINTMENT,
        brand_id="celine",
        occurred_on="2018-02-05",
        source=EventSource.NEWS,
        source_refs=("https://www.wwd.com/x",),
        status=EventStatus.CONFIRMED,
        attributes={"designer": "Hedi Slimane", "acclaim": "acclaimed"},
    )
    targets = target_strata(event, ctx.gazetteer)
    assert [(t.kind, t.stratum) for t in targets] == [
        (TargetKind.BRAND, "celine"),
        (TargetKind.PREDECESSOR_ERA, "celine/philo"),
    ]


def test_fr5_appointment_of_an_unknown_designer_closes_the_era_in_effect(ctx):
    event = make_event(
        event_type=EventType.DESIGNER_APPOINTMENT,
        brand_id="celine",
        occurred_on="2019-06-03",
        source=EventSource.MANUAL,
        source_refs=("manual:x",),
        status=EventStatus.CONFIRMED,
        attributes={"designer": "Someone New", "acclaim": "unproven"},
    )
    targets = target_strata(event, ctx.gazetteer)
    assert targets[1].stratum == "celine/slimane"


def test_fr5_collab_targets_both_brands_when_the_counterparty_resolves(ctx):
    event = make_event(
        event_type=EventType.COLLAB_ANNOUNCEMENT,
        brand_id="off-white",
        occurred_on=START,
        source=EventSource.NEWS,
        source_refs=("https://hypebeast.com/x",),
        status=EventStatus.CONFIRMED,
        attributes={"counterparty": "Rick Owens"},
    )
    assert [t.stratum for t in target_strata(event, ctx.gazetteer)] == ["off-white", "rick-owens"]

    unresolvable = make_event(
        event_type=EventType.COLLAB_ANNOUNCEMENT,
        brand_id="off-white",
        occurred_on=START,
        source=EventSource.NEWS,
        source_refs=("https://hypebeast.com/y",),
        status=EventStatus.CONFIRMED,
        attributes={"counterparty": "A Sneaker Company"},
    )
    assert [t.stratum for t in target_strata(unresolvable, ctx.gazetteer)] == ["off-white"]


def test_fr5_cosign_targets_the_most_specific_stratum_given(ctx):
    brand_only = cosign(occurred_on=START, celebrity="A")
    with_era = cosign(occurred_on=START, celebrity="B", era_id=ERA)
    with_category = cosign(occurred_on=START, celebrity="C", era_id=ERA, category="outerwear")
    assert target_strata(brand_only, ctx.gazetteer)[0].stratum == BRAND
    assert target_strata(with_era, ctx.gazetteer)[0].stratum == "helmut-lang/helmut"
    assert target_strata(with_category, ctx.gazetteer)[0].stratum == "helmut-lang/helmut/outerwear"


def test_fr5_scandal_and_runway_target_the_whole_brand(ctx):
    assert [t.stratum for t in target_strata(scandal(occurred_on=START), ctx.gazetteer)] == [BRAND]
    runway = make_event(
        event_type=EventType.RUNWAY_RECEPTION,
        brand_id=BRAND,
        occurred_on=START,
        source=EventSource.SOCIAL,
        source_refs=("social:show",),
        status=EventStatus.CONFIRMED,
        attributes={"polarity": "panned"},
    )
    assert [t.stratum for t in target_strata(runway, ctx.gazetteer)] == [BRAND]


def test_fr5_event_applies_to_a_garment_by_stratum_prefix(ctx):
    era_event = departure(occurred_on=START)
    assert applies_to(era_event, "helmut-lang/helmut/outerwear", ctx.gazetteer)
    assert not applies_to(era_event, "helmut-lang/post/outerwear", ctx.gazetteer)
    assert applies_to(scandal(occurred_on=START), "helmut-lang/post/denim", ctx.gazetteer)


def test_fr5_pending_and_rejected_events_never_contribute(ctx):
    leaf = "helmut-lang/helmut/outerwear"
    pending = departure(occurred_on=START, status=EventStatus.PENDING)
    legs = active_legs(
        [pending],
        leaf=leaf,
        as_of_week=week(1),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    assert legs == ()
    confirmed = transition_status(pending, EventStatus.CONFIRMED)
    legs = active_legs(
        [confirmed],
        leaf=leaf,
        as_of_week=week(1),
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=ctx.settings,
    )
    assert len(legs) == 1


def test_fr5_only_pending_events_can_transition():
    confirmed = departure(occurred_on=START)
    with pytest.raises(ValueError, match="only pending events"):
        transition_status(confirmed, EventStatus.REJECTED)
    rejected = transition_status(
        departure(occurred_on=START, status=EventStatus.PENDING), EventStatus.REJECTED
    )
    assert rejected.status is EventStatus.REJECTED


def test_fr5_a_rejected_event_is_not_resurrected_by_a_second_feed():
    rejected = transition_status(
        departure(occurred_on=START, status=EventStatus.PENDING), EventStatus.REJECTED
    )
    incoming = departure(occurred_on=START, refs=("https://www.wwd.com/z",))
    assert merge_event(rejected, incoming).status is EventStatus.REJECTED


def test_fr5_unknown_attributes_are_rejected():
    with pytest.raises(ValidationError, match="unknown attributes"):
        make_event(
            event_type=EventType.BRAND_SCANDAL,
            brand_id=BRAND,
            occurred_on=START,
            source=EventSource.NEWS,
            source_refs=("https://www.wwd.com/a",),
            status=EventStatus.CONFIRMED,
            attributes={"severity": "minor", "vibes": "bad"},
        )


def test_fr5_departure_requires_the_era_it_closes():
    with pytest.raises(ValidationError, match="requires the era_id"):
        make_event(
            event_type=EventType.DESIGNER_DEPARTURE,
            brand_id=BRAND,
            occurred_on=START,
            source=EventSource.MANUAL,
            source_refs=("manual:x",),
            status=EventStatus.CONFIRMED,
            attributes={"reason": "death"},
        )


def test_fr5_event_id_must_be_content_derived():
    event = departure(occurred_on=START)
    with pytest.raises(ValidationError, match="not content-derived"):
        FashionEvent(**{**event.model_dump(), "id": "0" * 16})
