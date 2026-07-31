"""Request and response schemas for the API (SCOPE FR-14).

These are transport shapes only. Domain invariants live in
:mod:`tickerpress.engine.models`; a schema here never re-implements one, it only
declares what the wire carries and how a stored row is projected onto it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..engine.models import (
    Alias,
    AliasKind,
    Appearance,
    Article,
    Channel,
    Company,
    Delivery,
    DeliveryItem,
    DeliveryMode,
    DeliveryStatus,
    Feed,
    FetchStatus,
    IngestRun,
    MatchedVia,
    Mention,
    Story,
    Strength,
    TextField,
)
from ..services import DigestResult, Explanation

__all__ = [
    "AliasCreate",
    "AliasOut",
    "AppearanceOut",
    "ArticleDetailOut",
    "ArticleOut",
    "CompanyCreate",
    "CompanyOut",
    "CompanyPatch",
    "DeliveryItemOut",
    "DeliveryOut",
    "DigestOut",
    "DigestRequest",
    "ExplainOut",
    "FeedCreate",
    "FeedOut",
    "FeedPatch",
    "HealthOut",
    "IngestRequest",
    "IngestRunOut",
    "MentionOut",
    "StoryDetailOut",
    "StoryOut",
]


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------


class CompanyCreate(BaseModel):
    """``POST /companies`` (FR-1)."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    name: str
    mode: DeliveryMode = DeliveryMode.DIGEST
    min_relevance: int = Field(default=20, ge=0, le=100)
    alert_min_relevance: int = Field(default=60, ge=0, le=100)
    context_terms: list[str] = Field(default_factory=list)
    anti_terms: list[str] = Field(default_factory=list)
    auto_alias: bool = True
    aliases: list[str] = Field(default_factory=list)


class CompanyPatch(BaseModel):
    """``PATCH /companies/{ticker}`` — every field optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    mode: DeliveryMode | None = None
    min_relevance: int | None = Field(default=None, ge=0, le=100)
    alert_min_relevance: int | None = Field(default=None, ge=0, le=100)
    context_terms: list[str] | None = None
    anti_terms: list[str] | None = None


class AliasCreate(BaseModel):
    """``POST /companies/{ticker}/aliases`` (SCOPE API sketch).

    ``strength``/``prior`` may be null: the kind defaults of DATA_MODEL §2.2
    then apply. There is deliberately no threshold field (FR-6, non-goal 10).
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    kind: AliasKind
    strength: Strength | None = None
    prior: float | None = Field(default=None, ge=0.0, le=0.3)


class FeedCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str


class FeedPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    url: str | None = None
    enabled: bool | None = None


class IngestRequest(BaseModel):
    """``POST /ingest/runs`` (FR-2, FR-10)."""

    model_config = ConfigDict(extra="forbid")

    now: datetime | None = None
    feed_id: int | None = None
    deliver_alerts: bool = True
    alert_channel: Channel = Channel.CONSOLE


class DigestRequest(BaseModel):
    """``POST /digests`` (FR-9). ``dry_run`` persists nothing at all."""

    model_config = ConfigDict(extra="forbid")

    channel: Channel = Channel.CONSOLE
    now: datetime | None = None
    dry_run: bool = False


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str
    version: str
    companies: int
    feeds: int
    articles: int
    stories: int


class AliasOut(BaseModel):
    id: int
    company_ticker: str
    text: str
    kind: AliasKind
    strength: Strength
    prior: float
    generated: bool
    created_at: datetime

    @classmethod
    def of(cls, alias: Alias) -> AliasOut:
        return cls(
            id=int(alias.id or 0),
            company_ticker=alias.company_ticker,
            text=alias.text,
            kind=alias.kind,
            strength=alias.strength,
            prior=alias.prior,
            generated=alias.generated,
            created_at=alias.created_at,
        )


class CompanyOut(BaseModel):
    ticker: str
    name: str
    mode: DeliveryMode
    min_relevance: int
    alert_min_relevance: int
    context_terms: list[str]
    anti_terms: list[str]
    created_at: datetime
    aliases: list[AliasOut] = Field(default_factory=list)

    @classmethod
    def of(cls, company: Company, aliases: list[Alias]) -> CompanyOut:
        return cls(
            ticker=company.ticker,
            name=company.name,
            mode=company.mode,
            min_relevance=company.min_relevance,
            alert_min_relevance=company.alert_min_relevance,
            context_terms=list(company.context_terms),
            anti_terms=list(company.anti_terms),
            created_at=company.created_at,
            aliases=[AliasOut.of(alias) for alias in aliases],
        )


class FeedOut(BaseModel):
    id: int
    name: str
    url: str
    enabled: bool
    etag: str | None
    last_modified: str | None
    last_polled_at: datetime | None
    last_status: FetchStatus | None
    created_at: datetime

    @classmethod
    def of(cls, feed: Feed) -> FeedOut:
        return cls(
            id=int(feed.id or 0),
            name=feed.name,
            url=feed.url,
            enabled=feed.enabled,
            etag=feed.etag,
            last_modified=feed.last_modified,
            last_polled_at=feed.last_polled_at,
            last_status=feed.last_status,
            created_at=feed.created_at,
        )


class IngestRunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    feed_results: list[dict[str, Any]]
    articles_new: int
    candidates_total: int
    mentions_accepted: int
    stories_new: int
    alerts_sent: int
    engine_version: str

    @classmethod
    def of(cls, run: IngestRun) -> IngestRunOut:
        return cls(
            id=int(run.id or 0),
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status.value,
            feed_results=[result.model_dump(mode="json") for result in run.feed_results],
            articles_new=run.articles_new,
            candidates_total=run.candidates_total,
            mentions_accepted=run.mentions_accepted,
            stories_new=run.stories_new,
            alerts_sent=run.alerts_sent,
            engine_version=run.engine_version,
        )


class AppearanceOut(BaseModel):
    article_id: int
    company_ticker: str
    mention_count: int
    title_hit: bool
    lede_hit: bool
    relevance: int

    @classmethod
    def of(cls, appearance: Appearance) -> AppearanceOut:
        return cls(**appearance.model_dump())


class ArticleOut(BaseModel):
    id: int
    feed_id: int
    item_guid: str
    url: str
    canonical_url: str
    title: str
    summary: str
    content: str | None
    published_at: datetime
    published_source: str
    first_seen_at: datetime
    last_seen_at: datetime
    content_sha256: str
    token_count: int
    content_token_count: int
    story_id: int
    dedup_similarity: float | None

    @classmethod
    def of(cls, article: Article) -> ArticleOut:
        return cls(
            id=int(article.id or 0),
            feed_id=article.feed_id,
            item_guid=article.item_guid,
            url=article.url,
            canonical_url=article.canonical_url,
            title=article.title,
            summary=article.summary,
            content=article.content,
            published_at=article.published_at,
            published_source=article.published_source.value,
            first_seen_at=article.first_seen_at,
            last_seen_at=article.last_seen_at,
            content_sha256=article.content_sha256,
            token_count=article.token_count,
            content_token_count=article.content_token_count,
            story_id=article.story_id,
            dedup_similarity=article.dedup_similarity,
        )


class ArticleDetailOut(ArticleOut):
    appearances: list[AppearanceOut] = Field(default_factory=list)

    @classmethod
    def detail(cls, article: Article, appearances: list[Appearance]) -> ArticleDetailOut:
        return cls(
            **ArticleOut.of(article).model_dump(),
            appearances=[AppearanceOut.of(a) for a in appearances],
        )


class MentionOut(BaseModel):
    """One candidate exactly as stored — accepted or rejected (FR-13)."""

    id: int
    company_ticker: str
    alias_id: int | None
    alias_text: str | None
    alias_kind: AliasKind | None
    field: TextField
    char_start: int
    char_end: int
    surface: str
    matched_via: MatchedVia
    strength: Strength
    features: dict[str, float]
    score: float
    threshold: float
    accepted: bool
    engine_version: str

    @classmethod
    def of(cls, mention: Mention, alias: Alias | None) -> MentionOut:
        return cls(
            id=int(mention.id or 0),
            company_ticker=mention.company_ticker,
            alias_id=mention.alias_id,
            alias_text=alias.text if alias else None,
            alias_kind=alias.kind if alias else None,
            field=mention.field,
            char_start=mention.char_start,
            char_end=mention.char_end,
            surface=mention.surface,
            matched_via=mention.matched_via,
            strength=mention.strength,
            features={key: float(value) for key, value in mention.features.items()},
            score=mention.score,
            threshold=mention.threshold,
            accepted=mention.accepted,
            engine_version=mention.engine_version,
        )


class ExplainOut(BaseModel):
    """``GET /articles/{id}/explain`` — read from persisted rows only."""

    article_id: int
    title: str
    story_id: int
    joined_existing_story: bool
    dedup_similarity: float | None
    story_copy_count: int
    candidates: list[MentionOut]
    appearances: list[AppearanceOut]

    @classmethod
    def of(cls, explanation: Explanation) -> ExplainOut:
        return cls(
            article_id=int(explanation.article.id or 0),
            title=explanation.article.title,
            story_id=explanation.story.id or 0,
            joined_existing_story=explanation.joined_existing_story,
            dedup_similarity=explanation.dedup_similarity,
            story_copy_count=explanation.story_copy_count,
            candidates=[MentionOut.of(c.mention, c.alias) for c in explanation.candidates],
            appearances=[AppearanceOut.of(a) for a in explanation.appearances],
        )


class StoryOut(BaseModel):
    id: int
    created_at: datetime
    first_published_at: datetime
    representative_article_id: int
    article_count: int

    @classmethod
    def of(cls, story: Story, article_count: int) -> StoryOut:
        return cls(
            id=int(story.id or 0),
            created_at=story.created_at,
            first_published_at=story.first_published_at,
            representative_article_id=story.representative_article_id,
            article_count=article_count,
        )


class StoryDetailOut(StoryOut):
    members: list[ArticleOut] = Field(default_factory=list)
    relevance: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def detail(
        cls, story: Story, members: list[Article], relevance: dict[str, int]
    ) -> StoryDetailOut:
        return cls(
            **StoryOut.of(story, len(members)).model_dump(),
            members=[ArticleOut.of(article) for article in members],
            relevance=relevance,
        )


class DeliveryOut(BaseModel):
    id: int
    channel: Channel
    kind: str
    created_at: datetime
    status: DeliveryStatus
    subject: str
    error: str | None
    body_text: str | None = None

    @classmethod
    def of(cls, delivery: Delivery, *, with_body: bool = False) -> DeliveryOut:
        return cls(
            id=int(delivery.id or 0),
            channel=delivery.channel,
            kind=delivery.kind.value,
            created_at=delivery.created_at,
            status=delivery.status,
            subject=delivery.subject,
            error=delivery.error,
            body_text=delivery.body_text if with_body else None,
        )


class DeliveryItemOut(BaseModel):
    id: int
    delivery_id: int
    channel: Channel
    company_ticker: str
    story_id: int
    article_id: int
    relevance: int
    counted: bool

    @classmethod
    def of(cls, item: DeliveryItem) -> DeliveryItemOut:
        return cls(
            id=int(item.id or 0),
            delivery_id=item.delivery_id,
            channel=item.channel,
            company_ticker=item.company_ticker,
            story_id=item.story_id,
            article_id=item.article_id,
            relevance=item.relevance,
            counted=item.counted,
        )


class DigestItemOut(BaseModel):
    company_ticker: str
    story_id: int
    article_id: int
    relevance: int
    title: str
    url: str
    outlet: str
    copy_count: int
    matched_surfaces: list[str]


class DigestOut(BaseModel):
    """``POST /digests`` result. ``delivery`` is null on a dry run (FR-9)."""

    dry_run: bool
    delivery: DeliveryOut | None
    body_text: str
    items: list[DigestItemOut]

    @classmethod
    def of(cls, result: DigestResult) -> DigestOut:
        return cls(
            dry_run=result.dry_run,
            delivery=(
                DeliveryOut.of(result.delivery, with_body=True)
                if result.delivery is not None
                else None
            ),
            body_text=result.body or "",
            items=[
                DigestItemOut(
                    company_ticker=item.company_ticker,
                    story_id=item.story_id,
                    article_id=item.article_id,
                    relevance=item.relevance,
                    title=item.title,
                    url=item.url,
                    outlet=item.outlet,
                    copy_count=item.copy_count,
                    matched_surfaces=list(item.matched_surfaces),
                )
                for item in result.items
            ],
        )
