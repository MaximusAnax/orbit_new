"""FR-4: pattern engine, suppressors, stage cues, attributes and the merge rule."""

from __future__ import annotations

import pytest
from newsalpha.engine.cluster import build_clusters
from newsalpha.engine.extract import (
    build_events,
    extract_from_article,
    historical_reference,
    parse_money,
)
from newsalpha.models import EventType, Stage
from newsalpha_testkit import article

#: Shared boilerplate so two paraphrases of one story clear FR-2's 0.60 Jaccard
#: threshold -- exactly how real wire copy repeats itself across outlets.
TAIL = (
    "The companies said in separate statements that the announcement was made before the "
    "opening bell and that further details would be provided to shareholders in the usual "
    "way once the documentation is complete and the paperwork has been prepared."
)


def only(extracted, event_type):
    """The single extracted event of `event_type`."""
    matches = [e for e in extracted if e.event.event_type is event_type]
    assert len(matches) == 1, [e.event.event_type for e in extracted]
    return matches[0].event


def events_for(datasets, rows):
    by_id = {row.id: row for row in rows}
    clusters = build_clusters(rows)
    return build_events(clusters, by_id, datasets), clusters


def single_event(datasets, title, body, **kwargs):
    row = article(datasets, kwargs.pop("external_id", "a"), title, body, **kwargs)
    extracted, _ = events_for(datasets, [row])
    return extracted, row


def test_fr4_trigger_and_polarity_produce_a_typed_event(datasets):
    extracted, _ = single_event(
        datasets, "Nvidia beat estimates", "Nvidia beat estimates for the quarter."
    )
    assert len(extracted) == 1
    event = extracted[0].event
    assert event.event_type is EventType.earnings_surprise
    assert event.attributes["polarity"] == "beat"
    assert event.stage is Stage.confirmed


def test_fr4_articles_matching_no_pattern_produce_no_event(datasets):
    extracted, _ = single_event(
        datasets, "Quarterly preview", "Analysts will watch margins when the company reports."
    )
    assert extracted == []


def test_fr4_negation_denies_mna_and_suppresses_every_other_type(datasets):
    denied, _ = single_event(
        datasets,
        "Palantir denied merger talks",
        "Palantir denied reports it is in talks to acquire Snowflake.",
    )
    assert denied[0].event.stage is Stage.denied

    suppressed, _ = single_event(
        datasets,
        "Exchange denies delisting",
        "Kraken denied it will delist the token this month.",
    )
    assert suppressed == []


def test_fr4_hedge_cues_mark_a_rumor(datasets):
    extracted, _ = single_event(
        datasets,
        "Report: Microsoft in talks to acquire Datadog",
        "Microsoft is reportedly in talks to acquire Datadog, sources say.",
    )
    assert extracted[0].event.stage is Stage.rumored


def test_fr4_negation_beats_hedge_in_one_sentence(datasets):
    """Cue precedence: a sentence carrying both cues is a denial."""
    extracted, _ = single_event(
        datasets,
        "Denial",
        "Microsoft denied reports it is in talks to acquire Datadog, sources say.",
    )
    assert extracted[0].event.stage is Stage.denied


def test_fr4_historical_reference_guard_suppresses(datasets):
    assert historical_reference("five years after the 2016 hack", 2026, ("anniversary",))
    assert not historical_reference("the 2026 exploit", 2026, ("anniversary",))
    extracted, _ = single_event(
        datasets,
        "Looking back",
        "Five years after the 2016 hack, the bridge was exploited for $60 million.",
    )
    assert extracted == []


def test_fr4_metaphor_guard_suppresses(datasets):
    extracted, _ = single_event(
        datasets,
        "A growth hack for founders",
        "This growth hack has nothing to do with markets.",
    )
    assert extracted == []


def test_fr4_required_context_gates_broad_triggers(datasets):
    """'approved' alone must not fire; a regulator in the sentence makes it an event."""
    without, _ = single_event(
        datasets, "Board update", "The board approved the annual budget on Friday."
    )
    assert without == []
    with_agency, _ = single_event(
        datasets, "Approval", "The SEC approved the exchange's application on Friday."
    )
    assert with_agency[0].event.attributes["polarity"] == "favorable"
    assert with_agency[0].event.attributes["agency"] == "SEC"


def test_fr4_extraction_confidence_ladder(datasets):
    trigger_only, _ = single_event(
        datasets, "Deal", "Microsoft agreed to acquire a private robotics startup."
    )
    assert trigger_only[0].event.extraction_confidence == 0.70

    with_attribute, _ = single_event(
        datasets, "Deal", "Microsoft agreed to acquire Datadog for $18 billion."
    )
    assert with_attribute[0].event.extraction_confidence == 0.90

    all_attributes, _ = single_event(
        datasets,
        "Deal",
        "Microsoft agreed to acquire Datadog for $18 billion at a 24% premium.",
    )
    assert all_attributes[0].event.extraction_confidence == 0.95


def test_fr4_extraction_confidence_never_exceeds_the_cap(datasets):
    extracted, _ = single_event(
        datasets,
        "Exploit",
        "The Aave protocol was exploited in a bridge incident that drained $47 million.",
    )
    assert extracted[0].event.extraction_confidence <= 0.95


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("$47 million", 47_000_000.0),
        ("$1.5 billion", 1_500_000_000.0),
        ("$625,000", 625_000.0),
        ("$18 billion.", 18_000_000_000.0),
        ("not money", None),
    ],
)
def test_fr4_money_normalization(surface, expected):
    assert parse_money(surface) == expected


def test_fr4_evidence_quotes_equal_the_analysed_span(datasets):
    extracted, row = single_event(
        datasets, "Nvidia beat estimates", "Nvidia beat estimates for the quarter."
    )
    text = row.analysis_text
    for span in extracted[0].event.evidence:
        assert span.quote == text[span.start : span.end]
        assert span.article_id == row.id


def test_fr4_at_most_one_event_per_cluster_and_type(datasets):
    story = (
        "Nvidia beat estimates for the quarter as data-center revenue climbed sharply. "
        "The chipmaker said demand remains ahead of supply for the next two quarters."
    )
    rows = [
        article(datasets, "a", "Nvidia beat estimates", story),
        article(
            datasets,
            "b",
            "Nvidia beat estimates",
            story + " Shares rose.",
            domain="cnbc.example",
            published_at="2026-03-10T10:00:00Z",
        ),
    ]
    extracted, clusters = events_for(datasets, rows)
    assert len(clusters) == 1
    assert len(extracted) == 1
    # Evidence from both articles, appended in (published_at, article_id) order.
    assert {span.article_id for span in extracted[0].event.evidence} == {rows[0].id, rows[1].id}


def test_fr4_merge_stage_latest_wins(datasets):
    """A later denial supersedes an earlier rumor inside the same cluster."""
    story = "Microsoft is in talks to acquire Datadog. " + TAIL
    denial = "Microsoft denied it is in talks to acquire Datadog. " + TAIL
    rows = [
        article(datasets, "a", "Microsoft in talks to acquire Datadog", story),
        article(
            datasets,
            "b",
            "Microsoft in talks to acquire Datadog",
            denial,
            domain="cnbc.example",
            published_at="2026-03-10T18:00:00Z",
        ),
    ]
    extracted, clusters = events_for(datasets, rows)
    assert len(clusters) == 1, "the two versions must land in one cluster"
    assert only(extracted, EventType.mna).stage is Stage.denied


def test_fr4_merge_takes_max_extraction_confidence_and_first_non_null_attribute(datasets):
    story = "Microsoft agreed to acquire Datadog. " + TAIL
    richer = "Microsoft agreed to acquire Datadog for $18 billion. " + TAIL
    rows = [
        article(datasets, "a", "Microsoft agreed to acquire Datadog", story),
        article(
            datasets,
            "b",
            "Microsoft agreed to acquire Datadog",
            richer,
            domain="cnbc.example",
            published_at="2026-03-10T12:00:00Z",
        ),
    ]
    extracted, clusters = events_for(datasets, rows)
    assert len(clusters) == 1
    event = only(extracted, EventType.mna)
    assert event.extraction_confidence == 0.90
    assert event.attributes["deal_value_usd"] == 18_000_000_000.0


def test_fr4_event_id_is_derived_from_cluster_and_type(datasets):
    from newsalpha.engine.normalize import hex16

    extracted, clusters = events_for(
        datasets,
        [article(datasets, "a", "Nvidia beat estimates", "Nvidia beat estimates again.")],
    )
    event = extracted[0].event
    assert event.id == hex16(f"event|{clusters[0].id}|earnings_surprise")


def test_fr4_unknown_attributes_are_rejected_by_the_schema(datasets):
    from newsalpha.models import Event, EvidenceSpan

    with pytest.raises(ValueError, match="not valid for"):
        Event(
            id="0" * 16,
            cluster_id="1" * 16,
            event_type=EventType.listing,
            stage=Stage.confirmed,
            attributes={"polarity": "beat"},
            extraction_confidence=0.9,
            evidence=(EvidenceSpan(article_id="a", start=0, end=3, quote="abc"),),
            event_date="2026-03-10",
            observed_at="2026-03-10T08:00:00Z",
        )


def test_fr4_sentence_scoping_keeps_unrelated_numbers_out(datasets):
    """The premium in a *different* sentence is not attached to the trigger sentence."""
    extracted, _ = single_event(
        datasets,
        "Deal",
        "Microsoft agreed to acquire Datadog. The offer carries a 24% premium.",
    )
    assert "premium_pct" not in extracted[0].event.attributes


def test_fr4_extract_from_article_is_deterministic(datasets):
    row = article(
        datasets,
        "a",
        "Nvidia beat estimates",
        "Nvidia beat estimates. The SEC approved the filing.",
    )
    first = extract_from_article(row, datasets)
    second = extract_from_article(row, datasets)
    assert [m.pattern_id for m in first] == [m.pattern_id for m in second]
