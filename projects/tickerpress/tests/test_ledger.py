"""FR-11 — the exactly-once delivery ledger."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError
from tickerpress.engine.models import (
    Channel,
    Delivery,
    DeliveryItem,
    DeliveryKind,
    DeliveryStatus,
)
from tickerpress.services import TickerPressService
from tickerpress.store import InMemoryRepository
from tickerpress_testkit import NOW, rss_feed, rss_item

ITEMS = [
    rss_item(
        guid="g1",
        link="https://wireone.example.com/apple-q2",
        title="Apple Inc. beats March-quarter estimates",
        description="Apple Inc. (NASDAQ: AAPL) reported quarterly revenue.",
        pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
    ),
    rss_item(
        guid="g2",
        link="https://wireone.example.com/apple-store",
        title="Apple Inc. opens a flagship store",
        description="Apple Inc. said the store opens Friday.",
        pub_date="Sun, 01 Mar 2026 09:10:00 GMT",
    ),
]


def seed(service: TickerPressService) -> None:
    service.add_company("AAPL", "Apple Inc.")
    service.feed_source.add("file:///wire.xml", rss_feed(ITEMS))
    service.add_feed("Wire One", "file:///wire.xml")
    service.ingest(deliver_alerts=False)


def _delivery(channel: Channel = Channel.CONSOLE) -> Delivery:
    return Delivery(
        channel=channel,
        kind=DeliveryKind.DIGEST,
        created_at=NOW,
        status=DeliveryStatus.COMPOSED,
        subject="subject",
        body_text="body",
    )


def test_fr11_compose_persist_send_mark_lifecycle(service: TickerPressService) -> None:
    seed(service)
    result = service.run_digest(Channel.CONSOLE)
    assert result.delivery is not None
    stored = service.repository.get_delivery(result.delivery.id or 0)
    assert stored is not None
    assert stored.status is DeliveryStatus.SENT
    assert stored.error is None
    assert stored.body_text == result.body
    items = service.repository.list_delivery_items(delivery_id=stored.id)
    assert items and all(item.counted for item in items)


def test_fr11_items_are_uncounted_until_the_send_succeeds(repository) -> None:
    delivery, items = repository.record_delivery(_delivery(), [])
    assert delivery.status is DeliveryStatus.COMPOSED
    assert items == []
    stored = repository.get_delivery(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.COMPOSED


def test_fr11_a_failed_send_releases_its_stories(service: TickerPressService, notifier) -> None:
    seed(service)
    notifier.fail = True
    failed = service.run_digest(Channel.CONSOLE)
    assert failed.delivery is not None
    assert failed.delivery.status is DeliveryStatus.FAILED
    assert failed.delivery.error is not None
    assert failed.delivery.body_text  # body retained for audit
    assert not any(item.counted for item in service.repository.list_delivery_items())

    notifier.fail = False
    retried = service.run_digest(Channel.CONSOLE)
    assert retried.delivery is not None
    assert retried.delivery.status is DeliveryStatus.SENT
    assert retried.body == failed.delivery.body_text
    counted = [item for item in service.repository.list_delivery_items() if item.counted]
    assert {item.story_id for item in counted} == {1, 2}

    # ... and exactly once: a third run finds nothing left
    assert service.run_digest(Channel.CONSOLE).empty


def test_fr11_partial_unique_index_blocks_a_second_counted_row(
    service: TickerPressService,
) -> None:
    """Double delivery is a constraint violation, not a code-review hope."""

    seed(service)
    delivered = service.run_digest(Channel.CONSOLE)
    assert delivered.delivery is not None
    already = delivered.items[0]

    duplicate, _ = service.repository.record_delivery(
        _delivery(),
        [
            DeliveryItem(
                delivery_id=0,
                channel=Channel.CONSOLE,
                company_ticker=already.company_ticker,
                story_id=already.story_id,
                article_id=already.article_id,
                relevance=already.relevance,
            )
        ],
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.repository.mark_delivery_sent(duplicate.id or 0)

    # the failed transaction rolled back: the duplicate is still uncounted
    rolled_back = service.repository.get_delivery(duplicate.id or 0)
    assert rolled_back is not None and rolled_back.status is DeliveryStatus.COMPOSED
    assert not any(
        item.counted for item in service.repository.list_delivery_items(delivery_id=duplicate.id)
    )


def test_fr11_the_constraint_exists_in_the_in_memory_backend() -> None:
    repository = InMemoryRepository()
    try:
        rows = repository._query(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_ledger_exactly_once'"
        )
        assert rows
        assert "WHERE counted = 1" in rows[0]["sql"]
    finally:
        repository.close()


def test_fr11_different_channels_each_get_the_story_once(service: TickerPressService) -> None:
    seed(service)
    console = service.run_digest(Channel.CONSOLE)
    file_run = service.run_digest(Channel.FILE)
    assert console.delivery is not None and file_run.delivery is not None
    assert {item.story_id for item in console.items} == {item.story_id for item in file_run.items}
    assert service.run_digest(Channel.CONSOLE).empty
    assert service.run_digest(Channel.FILE).empty


def test_fr11_undelivered_query_ignores_uncounted_items(
    service: TickerPressService, notifier
) -> None:
    seed(service)
    notifier.fail = True
    service.run_digest(Channel.CONSOLE)
    remaining = service.repository.undelivered_stories(Channel.CONSOLE)
    assert len(remaining) == 2


def test_fr11_reingest_then_digest_delivers_nothing(service: TickerPressService) -> None:
    """US-6: the exactly-once promise across re-runs."""

    seed(service)
    first = service.run_digest(Channel.CONSOLE)
    assert first.delivery is not None
    run = service.ingest(deliver_alerts=False)
    assert run.articles_new == 0
    assert service.run_digest(Channel.CONSOLE).empty


def test_fr11_deliveries_are_append_only_models() -> None:
    delivery = _delivery()
    with pytest.raises(ValidationError):
        delivery.status = DeliveryStatus.SENT  # type: ignore[misc]


def test_fr11_failed_status_requires_an_error_and_vice_versa() -> None:
    with pytest.raises(ValidationError):
        Delivery(
            channel=Channel.CONSOLE,
            kind=DeliveryKind.DIGEST,
            created_at=NOW,
            status=DeliveryStatus.FAILED,
            subject="s",
            body_text="b",
            error=None,
        )
    with pytest.raises(ValidationError):
        Delivery(
            channel=Channel.CONSOLE,
            kind=DeliveryKind.DIGEST,
            created_at=NOW,
            status=DeliveryStatus.SENT,
            subject="s",
            body_text="b",
            error="boom",
        )


def test_fr11_a_delivery_is_always_persisted_as_composed_first(repository) -> None:
    with pytest.raises(ValueError):
        repository.record_delivery(
            _delivery().model_copy(update={"status": DeliveryStatus.SENT}), []
        )
