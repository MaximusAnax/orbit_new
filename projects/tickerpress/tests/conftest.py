"""Shared pytest fixtures. Everything here is offline and clock-injected."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from tickerpress.adapters.clock import FixedClock
from tickerpress.adapters.feeds_fixture import FixtureFeedSource
from tickerpress.adapters.notify import ComposedMessage, DeliveryError
from tickerpress.engine.models import Channel
from tickerpress.resources import Lexicons, load_lexicons
from tickerpress.services import NotifierRegistry, TickerPressService
from tickerpress.store import InMemoryRepository, SQLiteRepository
from tickerpress_testkit import NOW


@pytest.fixture(scope="session")
def lexicons() -> Lexicons:
    """The committed ``data/`` lexicons."""

    return load_lexicons()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def repository() -> Iterator[SQLiteRepository]:
    repo = InMemoryRepository()
    try:
        yield repo
    finally:
        repo.close()


class RecordingNotifier:
    """Offline notifier that captures messages (and can be told to fail)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[ComposedMessage] = []
        self.fail = fail

    def deliver(self, message: ComposedMessage) -> None:
        if self.fail:
            raise DeliveryError("stub notifier refuses to deliver")
        self.messages.append(message)


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def feed_source() -> FixtureFeedSource:
    return FixtureFeedSource()


@pytest.fixture
def service(
    repository: SQLiteRepository,
    clock: FixedClock,
    feed_source: FixtureFeedSource,
    notifier: RecordingNotifier,
    lexicons: Lexicons,
    tmp_path: Path,
) -> TickerPressService:
    registry = NotifierRegistry(outbox_dir=tmp_path / "outbox")
    for channel in (Channel.CONSOLE, Channel.EMAIL, Channel.WEBHOOK):
        registry.register(channel, notifier)
    return TickerPressService(
        repository,
        clock=clock,
        feed_source=feed_source,
        notifiers=registry,
        lexicons=lexicons,
    )
