"""FR-2 — feed registry, fetching and per-feed error isolation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tickerpress.adapters.feeds import FetchResult
from tickerpress.adapters.feeds_fixture import FixtureFeedSource
from tickerpress.adapters.feeds_rss import ALLOW_NETWORK_ENV, LiveRssFeedSource
from tickerpress.engine.models import Feed, FetchStatus, IngestStatus
from tickerpress.services import TickerPressService
from tickerpress_testkit import NOW, rss_feed, rss_item

GOOD = rss_feed(
    [
        rss_item(
            guid="g1",
            link="https://wireone.example.com/a",
            title="Apple Inc. beats estimates",
            description="Apple Inc. reported revenue.",
            pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
        )
    ]
)
BROKEN = b"<?xml version='1.0'?><rss><channel><item><title>unterminated"


def feed(url: str = "file:///wire.xml", **kwargs) -> Feed:
    return Feed(name=kwargs.pop("name", "Wire"), url=url, created_at=NOW, **kwargs)


# ---------------------------------------------------------------------------
# the fixture adapter
# ---------------------------------------------------------------------------


def test_fr2_fixture_source_reads_a_file_url(tmp_path) -> None:
    path = tmp_path / "wire.xml"
    path.write_bytes(GOOD)
    result = FixtureFeedSource().fetch(feed(path.as_uri()), NOW)
    assert result.status is FetchStatus.OK
    assert result.raw_bytes == GOOD


def test_fr2_fixture_source_relocates_a_moved_fixture_via_base_dir(tmp_path) -> None:
    """A committed fixture URL embeds an absolute path; base_dir rescues it."""

    (tmp_path / "wire.xml").write_bytes(GOOD)
    stale = "file:///somewhere/else/entirely/wire.xml"
    assert FixtureFeedSource().fetch(feed(stale), NOW).status is FetchStatus.ERROR
    result = FixtureFeedSource(base_dir=tmp_path).fetch(feed(stale), NOW)
    assert result.raw_bytes == GOOD


def test_fr2_fixture_source_reports_a_missing_file_as_an_error(tmp_path) -> None:
    result = FixtureFeedSource().fetch(feed((tmp_path / "nope.xml").as_uri()), NOW)
    assert result.status is FetchStatus.ERROR
    assert result.error is not None


def test_fr2_fixture_source_refuses_http_urls() -> None:
    result = FixtureFeedSource().fetch(feed("https://example.com/feed.xml"), NOW)
    assert result.status is FetchStatus.ERROR
    assert "file://" in (result.error or "")


def test_fr2_fetch_result_status_values() -> None:
    assert FetchResult.failure("boom").status is FetchStatus.ERROR
    assert FetchResult.unchanged().status is FetchStatus.NOT_MODIFIED
    assert FetchResult(status=FetchStatus.OK, raw_bytes=b"x").ok


# ---------------------------------------------------------------------------
# the live adapter (env-gated; never touched by the offline suite)
# ---------------------------------------------------------------------------


def test_fr2_live_adapter_is_off_unless_the_env_gate_is_set(monkeypatch) -> None:
    monkeypatch.delenv(ALLOW_NETWORK_ENV, raising=False)
    result = LiveRssFeedSource().fetch(feed("https://example.com/feed.xml"), NOW)
    assert result.status is FetchStatus.ERROR
    assert ALLOW_NETWORK_ENV in (result.error or "")


def test_fr2_live_adapter_reads_the_env_gate(monkeypatch) -> None:
    monkeypatch.setenv(ALLOW_NETWORK_ENV, "1")
    assert LiveRssFeedSource().network_allowed
    monkeypatch.setenv(ALLOW_NETWORK_ENV, "0")
    assert not LiveRssFeedSource().network_allowed


def test_fr2_live_adapter_only_polls_http_urls() -> None:
    result = LiveRssFeedSource(allow_network=True).fetch(feed("file:///wire.xml"), NOW)
    assert result.status is FetchStatus.ERROR
    assert "http(s)" in (result.error or "")


def test_fr2_live_adapter_enforces_minimum_poll_spacing() -> None:
    polled = feed("https://example.com/feed.xml", last_polled_at=NOW - timedelta(minutes=5))
    result = LiveRssFeedSource(allow_network=True).fetch(polled, NOW)
    assert result.status is FetchStatus.NOT_MODIFIED


# ---------------------------------------------------------------------------
# ingest behaviour
# ---------------------------------------------------------------------------


class StubSource:
    """Records the order feeds were fetched in and replays canned results."""

    def __init__(self, results: dict[str, FetchResult]) -> None:
        self.results = results
        self.seen: list[int] = []
        self.headers: list[tuple[str | None, str | None]] = []

    def fetch(self, feed_row: Feed, now: datetime) -> FetchResult:
        assert feed_row.id is not None
        self.seen.append(feed_row.id)
        self.headers.append((feed_row.etag, feed_row.last_modified))
        return self.results.get(feed_row.url, FetchResult.failure("no stub"))


def test_fr2_ingest_iterates_feeds_in_ascending_id(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    stub = StubSource(
        {f"file:///{n}.xml": FetchResult(status=FetchStatus.OK, raw_bytes=GOOD) for n in range(3)}
    )
    service.feed_source = stub
    for n in (2, 0, 1):
        service.add_feed(f"Feed {n}", f"file:///{n}.xml")
    service.ingest(deliver_alerts=False)
    assert stub.seen == [1, 2, 3]


def test_fr2_one_broken_feed_does_not_abort_the_run(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source.add("file:///broken.xml", BROKEN)
    service.feed_source.add("file:///good.xml", GOOD)
    service.add_feed("Broken", "file:///broken.xml")
    service.add_feed("Good", "file:///good.xml")

    run = service.ingest(deliver_alerts=False)
    assert run.status is IngestStatus.PARTIAL
    assert run.articles_new == 1
    by_feed = {result.feed_id: result for result in run.feed_results}
    assert by_feed[1].status is FetchStatus.ERROR
    assert by_feed[1].error is not None
    assert by_feed[2].status is FetchStatus.OK
    assert by_feed[2].items_new == 1


def test_fr2_all_feeds_failing_marks_the_run_failed(service: TickerPressService) -> None:
    service.feed_source.add("file:///broken.xml", BROKEN)
    service.add_feed("Broken", "file:///broken.xml")
    run = service.ingest(deliver_alerts=False)
    assert run.status is IngestStatus.FAILED


def test_fr2_a_run_with_no_feeds_succeeds(service: TickerPressService) -> None:
    run = service.ingest(deliver_alerts=False)
    assert run.status is IngestStatus.SUCCEEDED
    assert run.feed_results == []


def test_fr2_fetch_state_is_persisted_after_every_attempt(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source = StubSource(
        {
            "file:///wire.xml": FetchResult(
                status=FetchStatus.OK,
                raw_bytes=GOOD,
                etag='W/"abc"',
                last_modified="Sun, 01 Mar 2026 21:30:00 GMT",
            )
        }
    )
    service.add_feed("Wire", "file:///wire.xml")
    service.ingest(deliver_alerts=False)

    stored = service.repository.get_feed(1)
    assert stored is not None
    assert stored.etag == 'W/"abc"'
    assert stored.last_modified == "Sun, 01 Mar 2026 21:30:00 GMT"
    assert stored.last_polled_at == NOW
    assert stored.last_status is FetchStatus.OK


def test_fr2_stored_conditional_state_is_offered_to_the_next_fetch(
    service: TickerPressService,
) -> None:
    service.add_company("AAPL", "Apple Inc.")
    stub = StubSource(
        {"file:///wire.xml": FetchResult(status=FetchStatus.OK, raw_bytes=GOOD, etag='W/"abc"')}
    )
    service.feed_source = stub
    service.add_feed("Wire", "file:///wire.xml")
    service.ingest(deliver_alerts=False)
    service.ingest(deliver_alerts=False)
    assert stub.headers == [(None, None), ('W/"abc"', None)]


def test_fr2_not_modified_creates_no_articles(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source = StubSource({"file:///wire.xml": FetchResult.unchanged(etag='W/"abc"')})
    service.add_feed("Wire", "file:///wire.xml")
    run = service.ingest(deliver_alerts=False)
    assert run.articles_new == 0
    assert run.status is IngestStatus.SUCCEEDED
    assert run.feed_results[0].status is FetchStatus.NOT_MODIFIED


def test_fr2_disabled_feeds_are_skipped(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source.add("file:///wire.xml", GOOD)
    service.add_feed("Wire", "file:///wire.xml")
    service.set_feed_enabled(1, False)
    run = service.ingest(deliver_alerts=False)
    assert run.feed_results == []
    assert run.articles_new == 0


def test_fr2_a_single_feed_can_be_selected_by_name(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source.add("file:///a.xml", GOOD)
    service.feed_source.add("file:///b.xml", GOOD)
    service.add_feed("A", "file:///a.xml")
    service.add_feed("B", "file:///b.xml")
    run = service.ingest(deliver_alerts=False, feed_name="B")
    assert [result.feed_id for result in run.feed_results] == [2]


def test_fr2_feeds_with_articles_cannot_be_deleted(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source.add("file:///wire.xml", GOOD)
    service.add_feed("Wire", "file:///wire.xml")
    service.ingest(deliver_alerts=False)
    with pytest.raises(ValueError):
        service.repository.delete_feed(1)


def test_fr2_feed_urls_must_use_a_known_scheme() -> None:
    with pytest.raises(ValueError):
        Feed(name="Bad", url="gopher://example.com/feed", created_at=NOW)


def test_fr2_counters_reconcile_with_the_rows_created(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source.add("file:///wire.xml", GOOD)
    service.add_feed("Wire", "file:///wire.xml")
    run = service.ingest(deliver_alerts=False)

    articles = list(service.repository.iter_articles())
    mentions = [
        m for article in articles for m in service.repository.list_mentions(article.id or 0)
    ]
    assert run.articles_new == len(articles)
    assert run.candidates_total == len(mentions)
    assert run.mentions_accepted == sum(1 for m in mentions if m.accepted)
    assert run.stories_new == len({article.story_id for article in articles})
