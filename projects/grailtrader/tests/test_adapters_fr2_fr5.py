"""Adapters: the offline implementations that tests and evals use, plus the live seams."""

from __future__ import annotations

import json

import pytest
from grailtrader.adapters import FixtureListingsFeed, FixtureNewsFeed, FixtureSocialFeed
from grailtrader.adapters.listings_csv import CsvListingsFeed
from grailtrader.adapters.listingsfeed import ListingsFeed
from grailtrader.adapters.news_rss import FEEDS_ENV, RssNewsFeed, classify_headline
from grailtrader.adapters.newsfeed import NewsFeed
from grailtrader.adapters.social_manual import ManualSocialEntry
from grailtrader.adapters.socialfeed import SocialFeed
from grailtrader.engine.ingest import normalize_listings
from grailtrader.models import (
    Category,
    EventSource,
    EventStatus,
    EventType,
    ListingSource,
    ListingStatus,
)
from grailtrader_testkit import BRAND, ERA, START


def write_listings_jsonl(tmp_path, rows):
    path = tmp_path / "listings.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def listing_row(external_id: str, *, sold_at: str = "2024-01-10T00:00:00Z", **overrides):
    row = {
        "external_id": external_id,
        "brand_ref": BRAND,
        "era_ref": ERA,
        "category": "outerwear",
        "platform_condition": "Gently Used",
        "status": "sold",
        "listed_at": "2024-01-01T00:00:00Z",
        "sold_at": sold_at,
        "sold_price": 900.0,
        "currency": "USD",
    }
    row.update(overrides)
    return row


def test_fixture_listings_feed_is_deterministically_ordered(tmp_path):
    path = write_listings_jsonl(
        tmp_path,
        [
            listing_row("c", sold_at="2024-02-01T00:00:00Z"),
            listing_row("a", sold_at="2024-01-15T00:00:00Z"),
            listing_row("b", sold_at="2024-01-15T00:00:00Z"),
        ],
    )
    feed = FixtureListingsFeed(path)
    assert isinstance(feed, ListingsFeed)
    assert [row.external_id for row in feed.fetch()] == ["a", "b", "c"]
    assert feed.fetch() == feed.fetch()
    assert all(row.source is ListingSource.FIXTURE for row in feed.fetch())


def test_fixture_listings_feed_reports_the_offending_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps(listing_row("a")) + "\n" + '{"external_id": "b"}\n' + "not json\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"bad\.jsonl:2"):
        FixtureListingsFeed(path).fetch()
    path.write_text(json.dumps(listing_row("a")) + "\nnot json\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:2: invalid JSON"):
        FixtureListingsFeed(path).fetch()


def test_csv_listings_feed_maps_documented_columns(tmp_path, ctx):
    path = tmp_path / "comps.csv"
    path.write_text(
        "id,brand,designer_era,category,condition,status,listed,date_sold,price_sold,size\n"
        "grailed-1,Helmut Lang,Helmut Lang era,outerwear,Gently Used,sold,"
        '2024-06-03,2024-06-10,"$1,200.50",48\n',
        encoding="utf-8",
    )
    rows = CsvListingsFeed(path).fetch()
    assert len(rows) == 1
    assert rows[0].sold_price == pytest.approx(1200.50)
    assert rows[0].size == "48"
    listings, report = normalize_listings(rows, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    assert report.ingested == 1
    assert listings[0].source is ListingSource.CSV
    assert listings[0].stratum_path == "helmut-lang/helmut/outerwear"


def test_csv_listings_feed_keeps_ask_only_rows_out_of_the_index(tmp_path, ctx):
    path = tmp_path / "comps.csv"
    path.write_text(
        "id,brand,era,category,condition,status,listed_at,asking\n"
        "ask-1,Helmut Lang,helmut,outerwear,Excellent,active,2024-06-03,1500\n",
        encoding="utf-8",
    )
    listings, _ = normalize_listings(
        CsvListingsFeed(path).fetch(), gazetteer=ctx.gazetteer, mapper=ctx.mapper
    )
    assert listings[0].status is ListingStatus.ACTIVE
    assert listings[0].sold_price is None


def test_csv_listings_feed_names_missing_columns(tmp_path):
    path = tmp_path / "comps.csv"
    path.write_text("id,brand\nx,Helmut Lang\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required column"):
        CsvListingsFeed(path).fetch()


def test_fixture_news_feed_reads_typed_confirmed_events(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [
        {
            "event_type": "designer_departure",
            "brand_id": BRAND,
            "era_id": ERA,
            "attributes": {"reason": "resignation"},
            "occurred_on": "2024-03-04",
            "source": "news",
            "source_refs": ["https://www.wwd.com/a"],
            "notes": "founder exit",
        },
        {
            "event_type": "brand_scandal",
            "brand_id": "celine",
            "attributes": {"severity": "minor"},
            "occurred_on": "2024-05-06",
            "source": "news",
            "source_refs": ["https://www.voguebusiness.com/b"],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    feed = FixtureNewsFeed(path)
    assert isinstance(feed, NewsFeed)
    events = feed.fetch("2024-01-01", "2024-12-31")
    assert [e.event_type for e in events] == [EventType.DESIGNER_DEPARTURE, EventType.BRAND_SCANDAL]
    assert all(e.status is EventStatus.CONFIRMED for e in events)
    assert events[0].corroboration == 1
    windowed = feed.fetch("2024-04-01", "2024-12-31")
    assert [e.brand_id for e in windowed] == ["celine"]


def test_fixture_social_feed_defaults_to_the_social_source(tmp_path):
    path = tmp_path / "social.jsonl"
    path.write_text(
        json.dumps(
            {
                "event_type": "celebrity_cosign",
                "brand_id": BRAND,
                "era_id": ERA,
                "attributes": {"celebrity": "A Person", "tier": "a_list"},
                "occurred_on": "2024-03-04",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    feed = FixtureSocialFeed(path)
    assert isinstance(feed, SocialFeed)
    (event,) = feed.fetch("2024-01-01", "2024-12-31")
    assert event.source is EventSource.SOCIAL
    assert event.source_refs == ("social:social-1",)


def test_manual_social_entry_records_confirmed_events(ctx):
    entry = ManualSocialEntry(ctx.gazetteer)
    event = entry.add(
        event_type=EventType.CELEBRITY_COSIGN,
        brand_ref="HL",
        occurred_on=START,
        attributes={"celebrity": "A Person", "tier": "b_list"},
        era_id=ERA,
        slug="hl-cosign",
    )
    assert event.brand_id == BRAND
    assert event.status is EventStatus.CONFIRMED
    assert entry.fetch(START, START) == [event]
    assert entry.fetch("2030-01-01", "2030-12-31") == []


def test_rss_adapter_is_env_gated(monkeypatch, ctx):
    monkeypatch.delenv(FEEDS_ENV, raising=False)
    assert RssNewsFeed.is_configured() is False
    feed = RssNewsFeed(ctx.gazetteer)
    with pytest.raises(RuntimeError, match=FEEDS_ENV):
        feed.fetch("2024-01-01", "2024-12-31")
    monkeypatch.setenv(FEEDS_ENV, "https://example.com/feed.xml, https://other.com/rss")
    assert RssNewsFeed.is_configured() is True
    assert RssNewsFeed.configured_feeds() == (
        "https://example.com/feed.xml",
        "https://other.com/rss",
    )


@pytest.mark.parametrize(
    ("headline", "expected_type"),
    [
        ("Helmut Lang creative director steps down after five years", "designer_departure"),
        ("Off-White named creative director for menswear", "designer_appointment"),
        ("Rick Owens teams up with a sportswear label", "collab_announcement"),
        ("Balenciaga faces backlash over campaign", "brand_scandal"),
        ("Prada show panned by critics", "runway_reception"),
    ],
)
def test_rss_keyword_rules_classify_headlines(ctx, headline, expected_type):
    classified = classify_headline(headline, ctx.gazetteer)
    assert classified is not None
    assert classified[1].value == expected_type


def test_rss_classifier_declines_when_no_brand_or_rule_matches(ctx):
    assert classify_headline("A designer steps down somewhere", ctx.gazetteer) is None
    assert classify_headline("Helmut Lang opens a pop-up in Paris", ctx.gazetteer) is None


def test_rss_candidates_are_pending_and_never_influence_anything(ctx):
    feed = RssNewsFeed(ctx.gazetteer, feeds=("https://example.com/feed.xml",))
    entries = [
        ("2024-03-05", "Helmut Lang creative director steps down", "https://www.wwd.com/x"),
        ("2024-03-06", "Nothing relevant here", "https://www.wwd.com/y"),
        ("2023-01-01", "Helmut Lang creative director steps down", "https://www.wwd.com/z"),
    ]
    candidates = feed.candidates(entries, since="2024-01-01", until="2024-12-31")
    assert len(candidates) == 1
    assert candidates[0].status is EventStatus.PENDING
    assert candidates[0].brand_id == BRAND
    assert candidates[0].source_refs == ("https://www.wwd.com/x",)
    assert candidates[0].era_id is not None


def test_rss_adapter_uses_an_injected_parser_without_touching_the_network(ctx):
    class FakeEntry:
        title = "Helmut Lang creative director steps down"
        link = "https://www.wwd.com/x"
        published = "2024-03-05T00:00:00Z"

    class FakeParser:
        @staticmethod
        def parse(url):
            assert url == "https://example.com/feed.xml"
            return type("Parsed", (), {"entries": [FakeEntry()]})()

    feed = RssNewsFeed(ctx.gazetteer, feeds=("https://example.com/feed.xml",), parser=FakeParser)
    events = feed.fetch("2024-01-01", "2024-12-31")
    assert [e.status for e in events] == [EventStatus.PENDING]
    assert events[0].event_type is EventType.DESIGNER_DEPARTURE


def test_fixture_feed_categories_round_trip(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "event_type": "celebrity_cosign",
                "brand_id": BRAND,
                "era_id": ERA,
                "attributes": {
                    "celebrity": "A Person",
                    "tier": "niche",
                    "category": Category.DENIM.value,
                },
                "occurred_on": "2024-03-04",
                "source": "social",
                "source_refs": ["social:a"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (event,) = FixtureSocialFeed(path).fetch("2024-01-01", "2024-12-31")
    assert event.attributes["category"] == "denim"
