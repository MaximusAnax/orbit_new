"""FR-3 — RSS 2.0 / Atom subset parsing, identity and dates."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from tickerpress.engine.feedparse import FeedParseError, item_identity, parse_feed
from tickerpress.engine.models import PublishedSource
from tickerpress.engine.pipeline import prepare_article
from tickerpress_testkit import NOW, make_lexicons, rss_feed, rss_item

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Global Desk</title>
  <entry>
    <id>tag:globaldesk.example.com,2026:1</id>
    <title>Shell posts higher refining margins</title>
    <link rel="alternate" href="https://globaldesk.example.com/shell-margins"/>
    <summary>The energy group said margins improved.</summary>
    <content type="html">&lt;p&gt;Full text here.&lt;/p&gt;</content>
    <published>2026-03-01T08:00:00Z</published>
    <updated>2026-03-01T09:00:00Z</updated>
  </entry>
  <entry>
    <title>Second entry without an id</title>
    <link href="https://globaldesk.example.com/second"/>
    <updated>2026-03-01T10:30:00+02:00</updated>
  </entry>
</feed>
"""


def test_fr3_rss_subset_parses_all_supported_fields() -> None:
    raw = rss_feed(
        [
            rss_item(
                guid="wireone-1",
                link="https://wireone.example.com/a",
                title="Apple beats estimates",
                description="Apple Inc. reported revenue.",
                content="&lt;p&gt;Body text.&lt;/p&gt;",
                pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        ]
    )
    parsed = parse_feed(raw)
    assert parsed.kind == "rss"
    (item,) = parsed.items
    assert item.guid == "wireone-1"
    assert item.link == "https://wireone.example.com/a"
    assert item.title == "Apple beats estimates"
    assert item.description == "Apple Inc. reported revenue."
    assert item.content == "<p>Body text.</p>"
    assert item.published_at == datetime(2026, 3, 1, 21, 30, tzinfo=UTC)


def test_fr3_atom_subset_parses() -> None:
    parsed = parse_feed(ATOM)
    assert parsed.kind == "atom"
    first, second = parsed.items
    assert first.guid == "tag:globaldesk.example.com,2026:1"
    assert first.link == "https://globaldesk.example.com/shell-margins"
    assert first.content == "<p>Full text here.</p>"
    assert first.published_at == datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    # `published` wins over `updated`; the second entry falls back to `updated`.
    assert second.published_at == datetime(2026, 3, 1, 8, 30, tzinfo=UTC)


def test_fr3_atom_entry_without_id_falls_back_to_link_title_hash() -> None:
    parsed = parse_feed(ATOM)
    second = parsed.items[1]
    expected = hashlib.sha256(
        b"https://globaldesk.example.com/second\nSecond entry without an id"
    ).hexdigest()
    assert second.identity == expected


def test_fr3_guid_is_preferred_over_the_hash() -> None:
    assert item_identity("abc", "https://x.example/a", "Title") == "abc"
    assert item_identity("  ", "https://x.example/a", "Title") == item_identity(
        None, "https://x.example/a", "Title"
    )


def test_fr3_items_missing_link_and_title_are_skipped_with_a_reason() -> None:
    raw = rss_feed(
        [
            rss_item(guid="only-guid", description="no link, no title"),
            rss_item(guid="ok", link="https://x.example/a", title="Fine"),
        ]
    )
    parsed = parse_feed(raw)
    assert [item.guid for item in parsed.items] == ["ok"]
    assert len(parsed.skipped) == 1
    assert parsed.skipped[0].position == 0
    assert "neither link nor title" in parsed.skipped[0].reason


def test_fr3_items_are_returned_in_document_order() -> None:
    raw = rss_feed(
        [
            rss_item(guid=f"g{index}", title=f"Item {index}", link=f"https://x/{index}")
            for index in range(5)
        ]
    )
    parsed = parse_feed(raw)
    assert [item.guid for item in parsed.items] == ["g0", "g1", "g2", "g3", "g4"]
    assert [item.position for item in parsed.items] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    "raw_date,expected",
    [
        ("Sun, 01 Mar 2026 21:30:00 GMT", datetime(2026, 3, 1, 21, 30, tzinfo=UTC)),
        ("Sun, 01 Mar 2026 16:30:00 -0500", datetime(2026, 3, 1, 21, 30, tzinfo=UTC)),
        ("2026-03-01T21:30:00Z", datetime(2026, 3, 1, 21, 30, tzinfo=UTC)),
        ("2026-03-01T23:30:00+02:00", datetime(2026, 3, 1, 21, 30, tzinfo=UTC)),
    ],
)
def test_fr3_rfc822_and_rfc3339_dates_both_parse(raw_date: str, expected: datetime) -> None:
    raw = rss_feed([rss_item(guid="g", title="T", link="https://x/a", pub_date=raw_date)])
    assert parse_feed(raw).items[0].published_at == expected


def test_fr3_unparseable_date_is_treated_as_missing() -> None:
    raw = rss_feed([rss_item(guid="g", title="T", link="https://x/a", pub_date="not a date")])
    assert parse_feed(raw).items[0].published_at is None


def test_fr3_missing_pubdate_falls_back_to_ingest_now() -> None:
    raw = rss_feed([rss_item(guid="g", title="Apple rose", link="https://x/a")])
    item = parse_feed(raw).items[0]
    prepared = prepare_article(item, NOW, make_lexicons())
    assert prepared.published_at == NOW
    assert prepared.published_source is PublishedSource.FALLBACK


def test_fr3_feed_pubdate_is_marked_as_coming_from_the_feed() -> None:
    raw = rss_feed(
        [
            rss_item(
                guid="g",
                title="Apple rose",
                link="https://x/a",
                pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        ]
    )
    prepared = prepare_article(parse_feed(raw).items[0], NOW, make_lexicons())
    assert prepared.published_source is PublishedSource.FEED
    assert prepared.published_at == datetime(2026, 3, 1, 21, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    [b"", b"   ", b"<rss><channel><item><title>unclosed", b"<html><body>not a feed</body></html>"],
)
def test_fr3_malformed_documents_raise_feed_parse_error(raw: bytes) -> None:
    with pytest.raises(FeedParseError):
        parse_feed(raw)


def test_fr3_normalization_runs_over_parsed_html_content() -> None:
    raw = rss_feed(
        [
            rss_item(
                guid="g",
                title="Apple &amp; friends",
                link="https://x/a",
                description="&lt;p&gt;Body   with   spacing&lt;/p&gt;",
            )
        ]
    )
    prepared = prepare_article(parse_feed(raw).items[0], NOW, make_lexicons())
    assert prepared.title == "Apple & friends"
    assert prepared.summary == "Body with spacing"
