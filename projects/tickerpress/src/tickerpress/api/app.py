"""FastAPI application (SCOPE FR-14, API sketch).

Thin by construction: each route validates its payload, calls exactly one
:class:`~tickerpress.services.TickerPressService` method (or one repository
read), and serializes the result. No selection rule, no ordering law and no
scoring lives here — those are engine concerns, and the CLI calls the same
service methods so the two front ends cannot drift.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Response, status

from .. import __version__
from ..engine.models import Channel, normalize_ticker
from ..services import TickerPressService
from .deps import build_service, get_service
from .errors import Conflict, NotFound, install_error_handlers
from .schemas import (
    AliasCreate,
    AliasOut,
    ArticleDetailOut,
    ArticleOut,
    CompanyCreate,
    CompanyOut,
    CompanyPatch,
    DeliveryItemOut,
    DeliveryOut,
    DigestOut,
    DigestRequest,
    ExplainOut,
    FeedCreate,
    FeedOut,
    FeedPatch,
    HealthOut,
    IngestRequest,
    IngestRunOut,
    StoryDetailOut,
    StoryOut,
)

__all__ = ["create_app"]

DESCRIPTION = (
    "Watchlist news detection, syndication dedup and exactly-once delivery. "
    "Informational only — links to third-party news coverage. Not investment advice."
)


def create_app(
    service: TickerPressService | None = None,
    *,
    db_path: str | Path | None = None,
) -> FastAPI:
    """Build the app.

    ``service`` is injected by tests and by the eval suite; when it is omitted
    the app builds the default SQLite-backed service during startup, so
    importing this module never touches a filesystem.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = False
        if getattr(app.state, "service", None) is None:
            app.state.service = build_service(db_path)
            owned = True
        try:
            yield
        finally:
            if owned:
                app.state.service.repository.close()
                app.state.service = None

    app = FastAPI(
        title="TickerPress",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.service = service
    install_error_handlers(app)

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    def health(svc: TickerPressService = Depends(get_service)) -> HealthOut:
        repo = svc.repository
        return HealthOut(
            status="ok",
            version=__version__,
            companies=len(repo.list_companies()),
            feeds=len(repo.list_feeds()),
            articles=sum(1 for _ in repo.iter_articles()),
            stories=len(repo.list_stories()),
        )

    # ------------------------------------------------------------------
    # watchlist (FR-1)
    # ------------------------------------------------------------------

    @app.get("/companies", response_model=list[CompanyOut], tags=["watchlist"])
    def list_companies(svc: TickerPressService = Depends(get_service)) -> list[CompanyOut]:
        return [
            CompanyOut.of(company, svc.repository.list_aliases(company.ticker))
            for company in svc.repository.list_companies()
        ]

    @app.post(
        "/companies",
        response_model=CompanyOut,
        status_code=status.HTTP_201_CREATED,
        tags=["watchlist"],
    )
    def create_company(
        payload: CompanyCreate, svc: TickerPressService = Depends(get_service)
    ) -> CompanyOut:
        company, _ = svc.add_company(
            payload.ticker,
            payload.name,
            mode=payload.mode,
            min_relevance=payload.min_relevance,
            alert_min_relevance=payload.alert_min_relevance,
            context_terms=payload.context_terms,
            anti_terms=payload.anti_terms,
            auto_alias=payload.auto_alias,
            extra_aliases=payload.aliases,
        )
        return CompanyOut.of(company, svc.repository.list_aliases(company.ticker))

    @app.get("/companies/{ticker}", response_model=CompanyOut, tags=["watchlist"])
    def get_company(ticker: str, svc: TickerPressService = Depends(get_service)) -> CompanyOut:
        company = svc.get_company(ticker)
        return CompanyOut.of(company, svc.repository.list_aliases(company.ticker))

    @app.patch("/companies/{ticker}", response_model=CompanyOut, tags=["watchlist"])
    def patch_company(
        ticker: str, payload: CompanyPatch, svc: TickerPressService = Depends(get_service)
    ) -> CompanyOut:
        company = svc.set_company(
            ticker,
            name=payload.name,
            mode=payload.mode,
            min_relevance=payload.min_relevance,
            alert_min_relevance=payload.alert_min_relevance,
            context_terms=payload.context_terms,
            anti_terms=payload.anti_terms,
        )
        return CompanyOut.of(company, svc.repository.list_aliases(company.ticker))

    @app.delete("/companies/{ticker}", status_code=status.HTTP_204_NO_CONTENT, tags=["watchlist"])
    def delete_company(ticker: str, svc: TickerPressService = Depends(get_service)) -> Response:
        if not svc.remove_company(ticker):
            raise NotFound(f"unknown company {normalize_ticker(ticker)}")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/companies/{ticker}/aliases",
        response_model=AliasOut,
        status_code=status.HTTP_201_CREATED,
        tags=["watchlist"],
    )
    def create_alias(
        ticker: str, payload: AliasCreate, svc: TickerPressService = Depends(get_service)
    ) -> AliasOut:
        alias = svc.add_alias(
            ticker,
            payload.text,
            kind=payload.kind,
            strength=payload.strength,
            prior=payload.prior,
        )
        return AliasOut.of(alias)

    @app.delete(
        "/companies/{ticker}/aliases/{alias_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["watchlist"],
    )
    def delete_alias(
        ticker: str, alias_id: int, svc: TickerPressService = Depends(get_service)
    ) -> Response:
        if not svc.remove_alias(ticker, alias_id):
            raise NotFound(f"unknown alias {alias_id} for {normalize_ticker(ticker)}")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # feeds (FR-2)
    # ------------------------------------------------------------------

    @app.get("/feeds", response_model=list[FeedOut], tags=["feeds"])
    def list_feeds(svc: TickerPressService = Depends(get_service)) -> list[FeedOut]:
        return [FeedOut.of(feed) for feed in svc.repository.list_feeds()]

    @app.post("/feeds", response_model=FeedOut, status_code=status.HTTP_201_CREATED, tags=["feeds"])
    def create_feed(payload: FeedCreate, svc: TickerPressService = Depends(get_service)) -> FeedOut:
        return FeedOut.of(svc.add_feed(payload.name, payload.url))

    @app.patch("/feeds/{feed_id}", response_model=FeedOut, tags=["feeds"])
    def patch_feed(
        feed_id: int, payload: FeedPatch, svc: TickerPressService = Depends(get_service)
    ) -> FeedOut:
        feed = svc.repository.get_feed(feed_id)
        if feed is None:
            raise NotFound(f"unknown feed {feed_id}")
        return FeedOut.of(
            svc.set_feed(feed_id, name=payload.name, url=payload.url, enabled=payload.enabled)
        )

    @app.delete("/feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["feeds"])
    def delete_feed(feed_id: int, svc: TickerPressService = Depends(get_service)) -> Response:
        try:
            removed = svc.repository.delete_feed(feed_id)
        except ValueError as exc:
            raise Conflict(str(exc)) from exc
        if not removed:
            raise NotFound(f"unknown feed {feed_id}")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # ingest (FR-2 .. FR-10)
    # ------------------------------------------------------------------

    @app.post(
        "/ingest/runs",
        response_model=IngestRunOut,
        status_code=status.HTTP_201_CREATED,
        tags=["ingest"],
    )
    def create_ingest_run(
        payload: IngestRequest, svc: TickerPressService = Depends(get_service)
    ) -> IngestRunOut:
        feed_name: str | None = None
        if payload.feed_id is not None:
            feed = svc.repository.get_feed(payload.feed_id)
            if feed is None:
                raise NotFound(f"unknown feed {payload.feed_id}")
            feed_name = feed.name
        run = svc.ingest(
            now=payload.now,
            feed_name=feed_name,
            deliver_alerts=payload.deliver_alerts,
            alert_channel=payload.alert_channel,
        )
        return IngestRunOut.of(run)

    @app.get("/ingest/runs", response_model=list[IngestRunOut], tags=["ingest"])
    def list_ingest_runs(
        limit: int = Query(default=20, ge=1, le=500),
        svc: TickerPressService = Depends(get_service),
    ) -> list[IngestRunOut]:
        return [IngestRunOut.of(run) for run in svc.repository.list_ingest_runs(limit=limit)]

    @app.get("/ingest/runs/{run_id}", response_model=IngestRunOut, tags=["ingest"])
    def get_ingest_run(run_id: int, svc: TickerPressService = Depends(get_service)) -> IngestRunOut:
        run = svc.repository.get_ingest_run(run_id)
        if run is None:
            raise NotFound(f"unknown ingest run {run_id}")
        return IngestRunOut.of(run)

    # ------------------------------------------------------------------
    # archive (FR-4, FR-8, FR-13)
    # ------------------------------------------------------------------

    @app.get("/articles", response_model=list[ArticleOut], tags=["archive"])
    def list_articles(
        company: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        min_relevance: int | None = Query(default=None, ge=0, le=100),
        limit: int = Query(default=50, ge=1, le=500),
        svc: TickerPressService = Depends(get_service),
    ) -> list[ArticleOut]:
        articles = svc.repository.list_articles(
            company=normalize_ticker(company) if company else None,
            since=since,
            until=until,
            min_relevance=min_relevance,
            limit=limit,
        )
        return [ArticleOut.of(article) for article in articles]

    @app.get("/articles/{article_id}", response_model=ArticleDetailOut, tags=["archive"])
    def get_article(
        article_id: int, svc: TickerPressService = Depends(get_service)
    ) -> ArticleDetailOut:
        article = svc.repository.get_article(article_id)
        if article is None:
            raise NotFound(f"unknown article {article_id}")
        return ArticleDetailOut.detail(
            article, svc.repository.list_appearances(article_id=article_id)
        )

    @app.get("/articles/{article_id}/explain", response_model=ExplainOut, tags=["archive"])
    def explain_article(
        article_id: int,
        company: str | None = None,
        svc: TickerPressService = Depends(get_service),
    ) -> ExplainOut:
        if svc.repository.get_article(article_id) is None:
            raise NotFound(f"unknown article {article_id}")
        return ExplainOut.of(svc.explain(article_id, company=company))

    @app.get("/stories", response_model=list[StoryOut], tags=["archive"])
    def list_stories(
        company: str | None = None,
        since: datetime | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        svc: TickerPressService = Depends(get_service),
    ) -> list[StoryOut]:
        stories = svc.repository.list_stories(
            company=normalize_ticker(company) if company else None, since=since, limit=limit
        )
        return [
            StoryOut.of(story, svc.repository.story_copy_count(int(story.id or 0)))
            for story in stories
        ]

    @app.get("/stories/{story_id}", response_model=StoryDetailOut, tags=["archive"])
    def get_story(story_id: int, svc: TickerPressService = Depends(get_service)) -> StoryDetailOut:
        story = svc.repository.get_story(story_id)
        if story is None:
            raise NotFound(f"unknown story {story_id}")
        members = svc.repository.story_members(story_id)
        relevance = {
            row.company_ticker: row.relevance
            for row in svc.repository.story_relevances()
            if row.story_id == story_id
        }
        return StoryDetailOut.detail(story, members, relevance)

    # ------------------------------------------------------------------
    # delivery (FR-9, FR-11)
    # ------------------------------------------------------------------

    @app.post("/digests", response_model=DigestOut, tags=["delivery"])
    def run_digest(
        payload: DigestRequest,
        response: Response,
        svc: TickerPressService = Depends(get_service),
    ) -> DigestOut | Response:
        result = svc.run_digest(payload.channel, now=payload.now, dry_run=payload.dry_run)
        if result.empty:
            # FR-9: an empty selection composes nothing and records nothing.
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        response.status_code = status.HTTP_200_OK
        return DigestOut.of(result)

    @app.get("/digests", response_model=list[DeliveryOut], tags=["delivery"])
    def list_digests(
        channel: Channel | None = None,
        limit: int = Query(default=20, ge=1, le=500),
        svc: TickerPressService = Depends(get_service),
    ) -> list[DeliveryOut]:
        return [
            DeliveryOut.of(delivery)
            for delivery in svc.repository.list_deliveries(channel=channel, limit=limit)
        ]

    @app.get("/digests/{delivery_id}", response_model=DeliveryOut, tags=["delivery"])
    def get_digest(delivery_id: int, svc: TickerPressService = Depends(get_service)) -> DeliveryOut:
        delivery = svc.repository.get_delivery(delivery_id)
        if delivery is None:
            raise NotFound(f"unknown delivery {delivery_id}")
        return DeliveryOut.of(delivery, with_body=True)

    @app.get("/deliveries", response_model=list[DeliveryItemOut], tags=["delivery"])
    def list_delivery_items(
        company: str | None = None,
        story_id: int | None = None,
        channel: Channel | None = None,
        svc: TickerPressService = Depends(get_service),
    ) -> list[DeliveryItemOut]:
        items = svc.repository.list_delivery_items(
            company=normalize_ticker(company) if company else None,
            story_id=story_id,
            channel=channel,
        )
        return [DeliveryItemOut.of(item) for item in items]

    return app
