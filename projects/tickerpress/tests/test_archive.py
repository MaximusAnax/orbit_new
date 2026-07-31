"""FR-4 — article archive: canonical URLs, identity, immutability."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError
from tickerpress.engine.models import Article, PublishedSource
from tickerpress.engine.pipeline import canonicalize_url, content_hash
from tickerpress.services import TickerPressService
from tickerpress_testkit import DEFAULT_TRACKING, NOW, rss_feed, rss_item

TRACKING = DEFAULT_TRACKING


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://WireOne.Example.com/apple", "https://wireone.example.com/apple"),
        ("HTTPS://wireone.example.com/apple", "https://wireone.example.com/apple"),
        ("https://wireone.example.com:443/apple", "https://wireone.example.com/apple"),
        ("http://wireone.example.com:80/apple", "http://wireone.example.com/apple"),
        ("https://wireone.example.com:8443/apple", "https://wireone.example.com:8443/apple"),
        ("https://wireone.example.com/apple#section", "https://wireone.example.com/apple"),
        (
            "https://wireone.example.com/apple?utm_source=rss&utm_medium=feed",
            "https://wireone.example.com/apple",
        ),
        (
            "https://wireone.example.com/apple?fbclid=1&gclid=2&mc_cid=3&mc_eid=4&ref=x&cmpid=y",
            "https://wireone.example.com/apple",
        ),
        (
            "https://wireone.example.com/apple?b=2&a=1",
            "https://wireone.example.com/apple?a=1&b=2",
        ),
        (
            "https://wireone.example.com/apple?id=7&utm_campaign=spring#top",
            "https://wireone.example.com/apple?id=7",
        ),
        ("https://wireone.example.com/Apple/Q2", "https://wireone.example.com/Apple/Q2"),
        ("", ""),
    ],
)
def test_fr4_canonical_url_table(raw: str, expected: str) -> None:
    assert canonicalize_url(raw, TRACKING) == expected


def test_fr4_canonicalization_is_idempotent() -> None:
    once = canonicalize_url("https://A.example.com/x?utm_source=rss&b=1#frag", TRACKING)
    assert canonicalize_url(once, TRACKING) == once


def test_fr4_content_hash_is_over_normalized_fields() -> None:
    digest = content_hash("Title", "Summary", "Content")
    assert digest == hashlib.sha256(b"Title\nSummary\nContent").hexdigest()
    assert content_hash("Title", "Summary", None) == hashlib.sha256(b"Title\nSummary\n").hexdigest()


def test_fr4_articles_are_unique_per_feed_and_guid(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    raw = rss_feed(
        [
            rss_item(
                guid="dup",
                link="https://wireone.example.com/a",
                title="Apple Inc. beats estimates",
                pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        ]
    )
    service.feed_source.add("file:///wire.xml", raw)
    service.add_feed("Wire", "file:///wire.xml")

    first = service.ingest(deliver_alerts=False)
    second = service.ingest(deliver_alerts=False)
    assert first.articles_new == 1
    assert second.articles_new == 0
    assert len(list(service.repository.iter_articles())) == 1


def test_fr4_reingesting_changed_content_keeps_the_first_version(
    service: TickerPressService,
) -> None:
    """SCOPE D16: first-ingested content wins; only last_seen_at moves."""

    service.add_company("AAPL", "Apple Inc.")
    original = rss_feed(
        [
            rss_item(
                guid="same",
                link="https://wireone.example.com/a",
                title="Apple Inc. beats estimates",
                description="First version.",
                pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        ]
    )
    service.feed_source.add("file:///wire.xml", original)
    service.add_feed("Wire", "file:///wire.xml")
    service.ingest(deliver_alerts=False)
    before = service.repository.get_article(1)
    assert before is not None and before.summary == "First version."

    rewritten = rss_feed(
        [
            rss_item(
                guid="same",
                link="https://wireone.example.com/a",
                title="Apple Inc. beats estimates (developing)",
                description="Second version with more detail.",
                pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        ]
    )
    service.feed_source.add("file:///wire.xml", rewritten)
    service.clock.set("2026-03-03T09:00:00Z")
    service.ingest(deliver_alerts=False)

    after = service.repository.get_article(1)
    assert after is not None
    assert after.title == before.title
    assert after.summary == "First version."
    assert after.first_seen_at == before.first_seen_at
    assert after.last_seen_at > before.last_seen_at


def test_fr4_article_rows_are_immutable_models() -> None:
    article = Article(
        id=1,
        feed_id=1,
        item_guid="g",
        url="https://x/a",
        canonical_url="https://x/a",
        title="T",
        summary="S",
        content=None,
        published_at=NOW,
        published_source=PublishedSource.FEED,
        first_seen_at=NOW,
        last_seen_at=NOW,
        content_sha256="0" * 64,
        token_count=2,
        content_token_count=0,
        story_id=1,
    )
    with pytest.raises(ValidationError):
        article.title = "changed"  # type: ignore[misc]


def test_fr4_content_token_count_is_zero_exactly_when_content_is_empty() -> None:
    base = {
        "feed_id": 1,
        "item_guid": "g",
        "url": "https://x/a",
        "canonical_url": "https://x/a",
        "title": "T",
        "summary": "S",
        "published_at": NOW,
        "published_source": PublishedSource.FEED,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "content_sha256": "0" * 64,
        "token_count": 2,
        "story_id": 1,
    }
    Article(**base, content=None, content_token_count=0)
    Article(**base, content="some words here", content_token_count=3)
    with pytest.raises(ValidationError):
        Article(**base, content=None, content_token_count=3)
    with pytest.raises(ValidationError):
        Article(**base, content="some words here", content_token_count=0)


def test_fr4_archive_retains_the_fields_us2_promises(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    raw = rss_feed(
        [
            rss_item(
                guid="g1",
                link="https://wireone.example.com/apple-q2?utm_source=rss",
                title="Apple Inc. beats estimates",
                description="Revenue rose.",
                content="&lt;p&gt;More detail about the quarter.&lt;/p&gt;",
                pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        ]
    )
    service.feed_source.add("file:///wire.xml", raw)
    service.add_feed("Wire", "file:///wire.xml")
    service.ingest(deliver_alerts=False)

    article = service.repository.get_article(1)
    assert article is not None
    assert article.url == "https://wireone.example.com/apple-q2?utm_source=rss"
    assert article.canonical_url == "https://wireone.example.com/apple-q2"
    assert article.title == "Apple Inc. beats estimates"
    assert article.summary == "Revenue rose."
    assert article.content == "More detail about the quarter."
    assert article.published_source is PublishedSource.FEED
    assert len(article.content_sha256) == 64
    assert article.content_token_count == 5
    assert article.token_count > article.content_token_count
