"""FastAPI application (FR-12).

Thin by construction: each handler parses its request, calls one
`NewsAlphaService` (or repository) method, and serializes the result.  No rule
lives here -- `STATUS_BY_CODE` is the whole error contract, and the clock is read
only where a request omits a timestamp, because the process boundary is the one
place allowed to read it (CONVENTIONS layering rules).

The app binds localhost by default and carries no auth: single local profile, no
accounts, no multi-tenancy (SCOPE D-17).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .. import __version__, errors
from ..adapters import resolve_marketdata, resolve_newsfeed
from ..datasets import Datasets
from ..engine.normalize import iso_utc
from ..factory import DEFAULT_FIXTURE_FEED, DEFAULT_FIXTURE_MARKET, build_service
from ..models import BacktestParams, WatchlistItem
from ..service import NewsAlphaService
from ..store.base import Repository
from .schemas import (
    ArticleListResponse,
    ArticleOut,
    AssetListResponse,
    AssetOut,
    BacktestListResponse,
    BacktestRequest,
    BacktestResponse,
    BacktestRunOut,
    BriefOut,
    DigestResponse,
    EventLinkOut,
    EventListResponse,
    EventOut,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    PricesLoadRequest,
    PricesLoadResponse,
    RevisionsResponse,
    SignalListResponse,
    SignalOut,
    WatchlistMutationResponse,
    WatchlistResponse,
)

#: The whole error contract (FR-12): catalog code -> HTTP status.
STATUS_BY_CODE: dict[str, int] = {
    "unknown_article": 404,
    "unknown_event": 404,
    "unknown_signal": 404,
    "unknown_brief": 404,
    "unknown_backtest": 404,
    "unknown_asset": 404,
    "not_found": 404,
    "invalid_request": 422,
    "conflict": 409,
    "dataset_invalid": 500,
    "frame_check_failed": 500,
    "feed_unavailable": 503,
    "market_data_unavailable": 503,
    "internal_error": 500,
}


def _now_iso() -> str:
    return iso_utc(datetime.now(UTC))


def get_service(request: Request) -> NewsAlphaService:
    """Service dependency; tests override it with an in-memory-backed service."""
    return request.app.state.service


ServiceDep = Annotated[NewsAlphaService, Depends(get_service)]


def create_app(
    service: NewsAlphaService | None = None,
    *,
    db_path: str | Path | None = None,
    repository: Repository | None = None,
    datasets: Datasets | None = None,
) -> FastAPI:
    """Build the app. Callers may inject a fully wired service (tests do)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "service", None) is None:
            app.state.service = build_service(
                db_path=db_path, repository=repository, datasets=datasets
            )
        yield
        app.state.service.repository.close()

    app = FastAPI(
        title="NewsAlpha",
        version=__version__,
        summary="Typed financial-news events, linked assets, scored signals, honest backtests.",
        lifespan=lifespan,
    )
    app.state.service = service

    @app.exception_handler(errors.NewsAlphaError)
    async def _domain_error(_: Request, exc: errors.NewsAlphaError) -> JSONResponse:
        return JSONResponse(status_code=STATUS_BY_CODE.get(exc.code, 400), content=exc.payload())

    _register(app)
    return app


def _register(app: FastAPI) -> None:
    """Every route. Each one parses, delegates once, and serializes."""

    # -- health ------------------------------------------------------------ #

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health(service: ServiceDep) -> HealthResponse:
        datasets = service.datasets
        return HealthResponse(
            version=__version__,
            assets=len(datasets.assets),
            patterns=len(datasets.patterns),
            priors=len(datasets.priors),
            templates=len(datasets.templates.templates),
        )

    # -- ingest ------------------------------------------------------------ #

    @app.post("/ingest", response_model=IngestResponse, status_code=201, tags=["ingest"])
    def ingest(service: ServiceDep, payload: IngestRequest) -> IngestResponse:
        try:
            feed = resolve_newsfeed(payload.feed, path=payload.path or DEFAULT_FIXTURE_FEED)
            result = service.ingest(
                feed, as_of=payload.as_of, since=payload.since, until=payload.until
            )
        except Exception as exc:  # translated by the catalog, re-raised otherwise
            raise errors.from_exception(exc) from exc
        return IngestResponse(
            as_of=payload.as_of,
            articles=result.articles_new,
            articles_excluded=result.articles_excluded,
            clusters=result.clusters,
            events=result.events,
            signals_new=result.signals_new,
            revisions_new=result.revisions_new,
            briefs=result.briefs,
        )

    # -- articles ---------------------------------------------------------- #

    @app.get("/articles", response_model=ArticleListResponse, tags=["articles"])
    def list_articles(
        service: ServiceDep,
        since: str | None = None,
        domain: str | None = None,
        limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    ) -> ArticleListResponse:
        rows = service.repository.list_articles(since=since, domain=domain, limit=limit)
        return ArticleListResponse(
            count=len(rows), articles=[ArticleOut.model_validate(a.model_dump()) for a in rows]
        )

    @app.get("/articles/{article_id}", response_model=ArticleOut, tags=["articles"])
    def get_article(service: ServiceDep, article_id: str) -> ArticleOut:
        row = service.repository.get_article(article_id)
        if row is None:
            raise errors.UnknownArticleError(f"no article {article_id!r}", article_id=article_id)
        return ArticleOut.model_validate(row.model_dump())

    # -- events ------------------------------------------------------------ #

    @app.get("/events", response_model=EventListResponse, tags=["events"])
    def list_events(
        service: ServiceDep,
        type: str | None = None,
        asset: str | None = None,
        stage: str | None = None,
        since: str | None = None,
    ) -> EventListResponse:
        rows = service.repository.list_events(
            event_type=type, asset_id=asset, stage=stage, since=since
        )
        return EventListResponse(count=len(rows), events=[EventOut.of(e) for e in rows])

    @app.get("/events/{event_id}", response_model=EventOut, tags=["events"])
    def get_event(service: ServiceDep, event_id: str) -> EventOut:
        event = service.repository.get_event(event_id)
        if event is None:
            raise errors.UnknownEventError(f"no event {event_id!r}", event_id=event_id)
        links = service.repository.list_links(event_id)
        assets = service.datasets.assets
        return EventOut.of(
            event,
            [
                EventLinkOut.of(
                    link, assets[link.asset_id].name if link.asset_id in assets else None
                )
                for link in links
            ],
        )

    # -- signals ----------------------------------------------------------- #

    @app.get("/signals", response_model=SignalListResponse, tags=["signals"])
    def list_signals(
        service: ServiceDep,
        asset: str | None = None,
        min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
        direction: str | None = None,
        since: str | None = None,
        include_superseded: bool = False,
    ) -> SignalListResponse:
        rows = service.signals(
            asset_id=asset,
            direction=direction,
            min_confidence=min_confidence,
            since=since,
            include_superseded=include_superseded,
        )
        return SignalListResponse(
            count=len(rows),
            include_superseded=include_superseded,
            signals=[SignalOut.model_validate(s.model_dump()) for s in rows],
        )

    @app.get("/signals/{signal_id}", response_model=SignalOut, tags=["signals"])
    def get_signal(service: ServiceDep, signal_id: str) -> SignalOut:
        row = service.repository.get_signal(signal_id)
        if row is None:
            raise errors.UnknownSignalError(f"no signal {signal_id!r}", signal_id=signal_id)
        return SignalOut.model_validate(row.model_dump())

    @app.get("/signals/{signal_id}/revisions", response_model=RevisionsResponse, tags=["signals"])
    def signal_revisions(service: ServiceDep, signal_id: str) -> RevisionsResponse:
        row = service.repository.get_signal(signal_id)
        if row is None:
            raise errors.UnknownSignalError(f"no signal {signal_id!r}", signal_id=signal_id)
        chain = service.signal_revisions(signal_id)
        return RevisionsResponse(
            signal_key=row.signal_key,
            count=len(chain),
            revisions=[SignalOut.model_validate(s.model_dump()) for s in chain],
        )

    @app.get("/briefs/{signal_id}", response_model=BriefOut, tags=["signals"])
    def get_brief(service: ServiceDep, signal_id: str) -> BriefOut:
        row = service.repository.get_brief(signal_id)
        if row is None:
            raise errors.UnknownBriefError(
                f"no brief for signal {signal_id!r}", signal_id=signal_id
            )
        return BriefOut.model_validate(row.model_dump())

    # -- digest ------------------------------------------------------------ #

    @app.get("/digest", response_model=DigestResponse, tags=["digest"])
    def digest(
        service: ServiceDep,
        date: str | None = None,
        watchlist_only: bool = True,
        include_superseded: bool = False,
    ) -> DigestResponse:
        as_of_date = date or _now_iso()[:10]
        try:
            result = service.digest(
                as_of_date=as_of_date,
                watchlist_only=watchlist_only,
                include_superseded=include_superseded,
            )
        except ValueError as exc:
            raise errors.InvalidRequestError(str(exc)) from exc
        return DigestResponse.model_validate(result.model_dump())

    # -- assets & watchlist ------------------------------------------------ #

    @app.get("/assets", response_model=AssetListResponse, tags=["assets"])
    def list_assets(
        service: ServiceDep, kind: str | None = None, q: str | None = None
    ) -> AssetListResponse:
        rows = [
            asset
            for asset in service.datasets.assets.values()
            if (kind is None or asset.kind.value == kind) and _matches(asset, q)
        ]
        rows.sort(key=lambda a: a.id)
        return AssetListResponse(
            count=len(rows), assets=[AssetOut.model_validate(a.model_dump()) for a in rows]
        )

    @app.get("/assets/{asset_id:path}", response_model=AssetOut, tags=["assets"])
    def get_asset(service: ServiceDep, asset_id: str) -> AssetOut:
        asset = service.datasets.assets.get(asset_id)
        if asset is None:
            raise errors.UnknownAssetError(
                f"no asset {asset_id!r} in the committed gazetteer",
                asset_id=asset_id,
                suggestion=service.suggest_asset(asset_id),
            )
        return AssetOut.model_validate(asset.model_dump())

    @app.get("/watchlist", response_model=WatchlistResponse, tags=["watchlist"])
    def list_watchlist(service: ServiceDep) -> WatchlistResponse:
        rows = service.repository.list_watchlist()
        return WatchlistResponse(count=len(rows), items=rows)

    @app.put(
        "/watchlist/{asset_id:path}", response_model=WatchlistMutationResponse, tags=["watchlist"]
    )
    def add_watchlist(service: ServiceDep, asset_id: str) -> WatchlistMutationResponse:
        if asset_id not in service.datasets.assets:
            raise errors.UnknownAssetError(
                f"no asset {asset_id!r} in the committed gazetteer",
                asset_id=asset_id,
                suggestion=service.suggest_asset(asset_id),
            )
        _ensure_assets(service)
        added = service.repository.add_watchlist(
            WatchlistItem(asset_id=asset_id, added_at=_now_iso())
        )
        return WatchlistMutationResponse(asset_id=asset_id, added=added)

    @app.delete(
        "/watchlist/{asset_id:path}", response_model=WatchlistMutationResponse, tags=["watchlist"]
    )
    def remove_watchlist(service: ServiceDep, asset_id: str) -> WatchlistMutationResponse:
        removed = service.remove_watch(asset_id)
        if not removed:
            raise errors.UnknownAssetError(
                f"{asset_id!r} is not on the watchlist", asset_id=asset_id
            )
        return WatchlistMutationResponse(asset_id=asset_id, removed=True)

    # -- prices ------------------------------------------------------------ #

    @app.post("/prices/load", response_model=PricesLoadResponse, status_code=201, tags=["prices"])
    def load_prices(service: ServiceDep, payload: PricesLoadRequest) -> PricesLoadResponse:
        wanted = payload.assets or list(service.datasets.assets)
        try:
            market = resolve_marketdata(
                payload.source,
                directory=payload.directory or DEFAULT_FIXTURE_MARKET,
                benchmarks=service.datasets.benchmarks,
            )
            loaded = service.load_prices(
                market, assets=wanted, start=payload.start, end=payload.end
            )
        except Exception as exc:
            raise errors.from_exception(exc) from exc
        return PricesLoadResponse(source=payload.source, assets=len(wanted), bars=loaded)

    # -- backtests --------------------------------------------------------- #

    @app.post("/backtests", response_model=BacktestResponse, status_code=201, tags=["backtests"])
    def run_backtest(service: ServiceDep, payload: BacktestRequest) -> BacktestResponse:
        try:
            params = BacktestParams(
                start=payload.start,
                end=payload.end,
                min_confidence=payload.min_confidence,
                placebo_seed=payload.placebo_seed,
            )
            run, results = service.run_backtest(params, payload.as_of or _now_iso())
        except Exception as exc:
            raise errors.from_exception(exc) from exc
        return BacktestResponse(run=BacktestRunOut.of(run), results=results)

    @app.get("/backtests", response_model=BacktestListResponse, tags=["backtests"])
    def list_backtests(
        service: ServiceDep, limit: Annotated[int | None, Query(ge=1, le=200)] = None
    ) -> BacktestListResponse:
        rows = service.repository.list_backtests(limit)
        return BacktestListResponse(count=len(rows), runs=[BacktestRunOut.of(r) for r in rows])

    @app.get("/backtests/{run_id}", response_model=BacktestResponse, tags=["backtests"])
    def get_backtest(service: ServiceDep, run_id: str) -> BacktestResponse:
        run = service.repository.get_backtest(run_id)
        if run is None:
            raise errors.UnknownBacktestError(f"no backtest run {run_id!r}", run_id=run_id)
        return BacktestResponse(
            run=BacktestRunOut.of(run), results=service.repository.list_backtest_results(run_id)
        )


def _matches(asset: Any, query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    surfaces = [asset.id, asset.name, asset.symbol, *asset.aliases]
    return any(needle in surface.casefold() for surface in surfaces)


def _ensure_assets(service: NewsAlphaService) -> None:
    """The watchlist has an FK to `asset`; make sure the gazetteer is loaded."""
    if service.repository.get_asset(next(iter(service.datasets.assets))) is None:
        service.repository.replace_assets(list(service.datasets.assets.values()))


app = create_app()

__all__ = ["STATUS_BY_CODE", "app", "create_app", "get_service"]
