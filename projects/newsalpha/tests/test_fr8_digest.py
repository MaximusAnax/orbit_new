"""FR-8: digest window, watchlist filter, supersession filter and ranking."""

from __future__ import annotations

from newsalpha.engine import pipeline
from newsalpha.engine.digest import EMPTY_STATE, build_digest, in_window
from newsalpha_testkit import raw

AS_OF_1 = "2026-03-11T07:00:00Z"
AS_OF_2 = "2026-03-16T07:00:00Z"


def corpus():
    return [
        raw(
            "hack",
            "Aave bridge hack drains $47 million",
            "The Aave protocol was exploited in a bridge hack that drained $47 million.",
            domain="coindesk.example",
            published_at="2026-03-10T08:00:00Z",
        ),
        raw(
            "earn",
            "Nvidia beat estimates",
            "Nvidia beat estimates for the quarter as revenue climbed.",
            domain="cnbc.example",
            published_at="2026-03-10T09:00:00Z",
        ),
    ]


def test_fr8_window_is_exclusive_at_the_lower_bound():
    assert in_window("2026-03-16", "2026-03-16") is True
    assert in_window("2026-03-11", "2026-03-16") is False  # exactly 5 days earlier
    assert in_window("2026-03-12", "2026-03-16") is True
    assert in_window("2026-03-17", "2026-03-16") is False


def test_fr8_ranking_is_absolute_score_then_confidence_then_asset(datasets):
    _, result = pipeline.ingest(corpus(), datasets, as_of=AS_OF_1)
    digest = build_digest(
        list(result.new_signals), datasets, as_of_date="2026-03-10", watchlist_only=False
    )
    scores = [abs(entry.score) for entry in digest.entries]
    assert scores == sorted(scores, reverse=True)
    keys = [(-abs(e.score), -e.confidence, e.asset_id) for e in digest.entries]
    assert keys == sorted(keys)


def test_fr8_watchlist_filter_is_the_default(datasets):
    _, result = pipeline.ingest(corpus(), datasets, as_of=AS_OF_1)
    only_nvda = build_digest(
        list(result.new_signals),
        datasets,
        as_of_date="2026-03-10",
        watchlist={"eq:NVDA"},
    )
    assert {entry.asset_id for entry in only_nvda.entries} == {"eq:NVDA"}

    everything = build_digest(
        list(result.new_signals), datasets, as_of_date="2026-03-10", watchlist_only=False
    )
    assert {entry.asset_id for entry in everything.entries} >= {"eq:NVDA", "cx:AAVE"}


def test_fr8_empty_day_renders_an_explicit_state_not_an_error(datasets):
    digest = build_digest([], datasets, as_of_date="2026-03-10", watchlist_only=False)
    assert digest.entries == ()
    assert digest.empty_state == EMPTY_STATE


def test_fr8_entries_carry_a_one_line_summary_from_the_template_catalog(datasets):
    _, result = pipeline.ingest(corpus(), datasets, as_of=AS_OF_1)
    digest = build_digest(
        list(result.new_signals), datasets, as_of_date="2026-03-10", watchlist_only=False
    )
    entry = next(e for e in digest.entries if e.asset_id == "eq:NVDA")
    assert entry.summary.startswith("NVIDIA Corporation (NVDA)")
    assert "above expectations" in entry.summary


def test_fr8_supersession_denial_outranks_rumor(datasets):
    """A Monday rumor and a Friday denial: the digest shows the denial, hides the rumor."""
    rumor = raw(
        "r",
        "Microsoft in talks to acquire Datadog",
        "Microsoft is reportedly in talks to acquire Datadog, sources say.",
        published_at="2026-03-10T08:00:00Z",
    )
    denial = raw(
        "d",
        "Microsoft denies Datadog talks",
        "Microsoft denied it is in talks to acquire Datadog, the company said on Friday.",
        domain="cnbc.example",
        published_at="2026-03-14T08:00:00Z",
    )
    fresh1, first = pipeline.ingest([rumor], datasets, as_of=AS_OF_1)
    _, second = pipeline.ingest(
        [denial],
        datasets,
        as_of=AS_OF_2,
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    signals = [*first.new_signals, *second.new_signals]

    digest = build_digest(signals, datasets, as_of_date="2026-03-14", watchlist_only=False)
    shown = {(e.asset_id, e.direction.value) for e in digest.entries}
    assert ("eq:DDOG", "bearish") in shown
    assert ("eq:DDOG", "bullish") not in shown

    annotated = next(e for e in digest.entries if e.asset_id == "eq:DDOG")
    assert annotated.supersession_note is not None
    assert "rumored" in annotated.supersession_note

    with_superseded = build_digest(
        signals,
        datasets,
        as_of_date="2026-03-14",
        watchlist_only=False,
        include_superseded=True,
    )
    assert len(with_superseded.entries) > len(digest.entries)


def test_fr8_only_the_latest_revision_appears(datasets):
    story = (
        "The Aave protocol was exploited in a bridge hack that drained $47 million from "
        "the cross-chain bridge early on Saturday, the team said in a statement."
    )
    first_report = raw(
        "a",
        "Aave bridge hack",
        story,
        domain="coindesk.example",
        published_at="2026-03-10T08:00:00Z",
    )
    second_report = raw(
        "b",
        "Aave bridge hack",
        story + " Users were told to wait.",
        domain="theblock.example",
        published_at="2026-03-10T18:00:00Z",
    )
    fresh1, first = pipeline.ingest([first_report], datasets, as_of=AS_OF_1)
    _, second = pipeline.ingest(
        [second_report],
        datasets,
        as_of="2026-03-12T07:00:00Z",
        existing_articles=fresh1,
        existing_signals=list(first.new_signals),
    )
    digest = build_digest(
        [*first.new_signals, *second.new_signals],
        datasets,
        as_of_date="2026-03-10",
        watchlist_only=False,
    )
    aave = [e for e in digest.entries if e.asset_id == "cx:AAVE"]
    assert len(aave) == 1
    assert aave[0].signal_id == second.new_signals[0].id


def test_fr8_digest_has_no_imperative_field(datasets):
    """US-4: the schema itself cannot carry a trade instruction."""
    from newsalpha.models import DigestEntry, Signal

    banned = {"action", "recommendation", "advice", "trade", "position", "size"}
    assert banned & set(DigestEntry.model_fields) == set()
    assert banned & set(Signal.model_fields) == set()
