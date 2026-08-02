"""Repository port (DATA_MODEL §4).

One protocol, one implementation (:class:`~tickerpress.store.sqlite_store.SQLiteRepository`).
``InMemoryRepository`` is a factory returning that same class against
``":memory:"`` — deliberately *not* a second, dict-backed backend, because the
exactly-once contract is a partial unique index and a dict store would silently
lack it, making ledger tests backend-dependent.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..engine.models import (
    Alias,
    Appearance,
    Article,
    Channel,
    Company,
    Delivery,
    DeliveryItem,
    Feed,
    IngestRun,
    Mention,
    Story,
)

__all__ = ["ArticleTextRow", "Repository", "StoryRelevance", "UndeliveredStory"]


@dataclass(frozen=True, slots=True)
class ArticleTextRow:
    """The archived text dedup needs; shingles are recomputed, never stored."""

    article_id: int
    story_id: int
    published_at: datetime
    canonical_url: str
    content_sha256: str
    title: str
    summary: str
    content: str | None


@dataclass(frozen=True, slots=True)
class StoryRelevance:
    """Derived story-level relevance: ``MAX(relevance)`` over member appearances."""

    story_id: int
    company_ticker: str
    relevance: int


@dataclass(frozen=True, slots=True)
class UndeliveredStory:
    """One (company, story) pair not yet counted on a channel's ledger."""

    company_ticker: str
    story_id: int
    relevance: int
    article_id: int
    title: str
    url: str
    outlet: str
    published_at: datetime
    first_published_at: datetime
    copy_count: int
    matched_surfaces: tuple[str, ...]


@runtime_checkable
class Repository(Protocol):
    """Persistence for every entity in DATA_MODEL §2."""

    # -- lifecycle ------------------------------------------------------
    def initialize(self) -> None: ...
    def close(self) -> None: ...
    def transaction(self) -> AbstractContextManager[None]: ...

    # -- companies and aliases -----------------------------------------
    def add_company(self, company: Company) -> Company: ...
    def get_company(self, ticker: str) -> Company | None: ...
    def list_companies(self) -> list[Company]: ...
    def update_company(self, company: Company) -> Company: ...
    def delete_company(self, ticker: str) -> bool: ...

    def add_alias(self, alias: Alias) -> Alias: ...
    def get_alias(self, alias_id: int) -> Alias | None: ...
    def list_aliases(self, ticker: str | None = None) -> list[Alias]: ...
    def delete_alias(self, alias_id: int) -> bool: ...

    # -- feeds -----------------------------------------------------------
    def add_feed(self, feed: Feed) -> Feed: ...
    def get_feed(self, feed_id: int) -> Feed | None: ...
    def get_feed_by_name(self, name: str) -> Feed | None: ...
    def list_feeds(self, *, enabled_only: bool = False) -> list[Feed]: ...
    def update_feed(self, feed: Feed) -> Feed: ...
    def delete_feed(self, feed_id: int) -> bool: ...

    # -- stories and articles --------------------------------------------
    def create_story(
        self, created_at: datetime, first_published_at: datetime, representative_article_id: int
    ) -> int: ...
    def get_story(self, story_id: int) -> Story | None: ...
    def update_story(self, story: Story) -> Story: ...
    def list_stories(
        self, *, company: str | None = None, since: datetime | None = None, limit: int | None = None
    ) -> list[Story]: ...
    def story_members(self, story_id: int) -> list[Article]: ...
    def story_copy_count(self, story_id: int) -> int: ...

    def insert_article(self, article: Article) -> Article: ...
    def get_article(self, article_id: int) -> Article | None: ...
    def get_article_by_guid(self, feed_id: int, item_guid: str) -> Article | None: ...
    def touch_article(self, article_id: int, last_seen_at: datetime) -> None: ...
    def articles_in_window(
        self, published_at: datetime, window_days: int
    ) -> list[ArticleTextRow]: ...
    def list_articles(
        self,
        *,
        company: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        min_relevance: int | None = None,
        limit: int | None = None,
    ) -> list[Article]: ...

    # -- mentions and appearances -----------------------------------------
    def insert_mentions(self, mentions: Sequence[Mention]) -> list[Mention]: ...
    def list_mentions(
        self, article_id: int, *, company: str | None = None, accepted_only: bool = False
    ) -> list[Mention]: ...
    def insert_appearances(self, appearances: Sequence[Appearance]) -> list[Appearance]: ...
    def get_appearance(self, article_id: int, company: str) -> Appearance | None: ...
    def list_appearances(
        self, *, article_id: int | None = None, company: str | None = None
    ) -> list[Appearance]: ...
    def story_relevances(self, *, company: str | None = None) -> list[StoryRelevance]: ...

    # -- ingest runs --------------------------------------------------------
    def start_ingest_run(self, run: IngestRun) -> IngestRun: ...
    def finish_ingest_run(self, run: IngestRun) -> IngestRun: ...
    def get_ingest_run(self, run_id: int) -> IngestRun | None: ...
    def list_ingest_runs(self, *, limit: int | None = None) -> list[IngestRun]: ...

    # -- delivery ledger -----------------------------------------------------
    def undelivered_stories(
        self, channel: Channel, *, company: str | None = None
    ) -> list[UndeliveredStory]: ...
    def record_delivery(
        self, delivery: Delivery, items: Sequence[DeliveryItem]
    ) -> tuple[Delivery, list[DeliveryItem]]: ...
    def mark_delivery_sent(self, delivery_id: int) -> Delivery: ...
    def mark_delivery_failed(self, delivery_id: int, error: str) -> Delivery: ...
    def get_delivery(self, delivery_id: int) -> Delivery | None: ...
    def list_deliveries(
        self, *, channel: Channel | None = None, limit: int | None = None
    ) -> list[Delivery]: ...
    def list_delivery_items(
        self,
        *,
        delivery_id: int | None = None,
        channel: Channel | None = None,
        company: str | None = None,
        story_id: int | None = None,
    ) -> list[DeliveryItem]: ...

    def iter_articles(self) -> Iterator[Article]: ...
