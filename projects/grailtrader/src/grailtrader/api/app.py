"""The FastAPI application (FR-12) — thin: parse, delegate, serialize.

Every route calls :class:`~grailtrader.service.GrailTraderService`; no business
rule lives here. Errors are raised by the service and turned into the documented
catalog by :mod:`grailtrader.api.errors`.

Run it with::

    uv run uvicorn grailtrader.api:app --port 8000
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Response, status

from ..models import (
    Advice,
    AdviceAction,
    Brand,
    EventStatus,
    EventType,
    FashionEvent,
    Garment,
    Listing,
    ListingStatus,
    ValuationMethod,
)
from ..service import GrailTraderService, open_repository
from ..weeks import week_key
from .errors import ErrorResponse, install_error_handlers
from .schemas import (
    AdviceBatchResponse,
    AdviseRequest,
    BacktestRequest,
    BacktestRunResponse,
    EventCreateRequest,
    EventDetailResponse,
    EventIngestRequest,
    EventIngestResponse,
    GarmentCreateRequest,
    GarmentListResponse,
    GarmentPatchRequest,
    HealthResponse,
    IndexBuildRequest,
    IndexBuildResponse,
    IndexSeriesResponse,
    ListingsLoadRequest,
    ListingsLoadResponse,
    PortfolioValuationResponse,
    StrataResponse,
    ValuationResponse,
)

__all__ = ["app", "create_app", "get_service"]

_service: GrailTraderService | None = None


def get_service() -> GrailTraderService:
    """The process-wide service. Tests override this dependency with an in-memory one."""
    global _service
    if _service is None:
        repo = open_repository(os.environ.get("GRAILTRADER_DB"))
        repo.initialize()
        _service = GrailTraderService(repo)
    return _service


ServiceDep = Annotated[GrailTraderService, Depends(get_service)]

RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "not_found"},
    409: {"model": ErrorResponse, "description": "precondition_failed / conflict"},
    422: {"model": ErrorResponse, "description": "invalid_request / unknown_reference"},
}


def create_app() -> FastAPI:
    app = FastAPI(
        title="GrailTrader",
        version="0.1.0",
        summary="Buy/sell/hold guidance for second-hand designer clothing.",
        description=(
            "Indices from secondhand sold comps, typed fashion events with decaying "
            "impact priors, and per-garment advice with confidence, drivers and a "
            "collectibles-not-securities frame check. Local, single-user, read-only "
            "with respect to any marketplace: this service never buys or sells anything."
        ),
        responses=RESPONSES,
    )
    install_error_handlers(app)

    # -- health ------------------------------------------------------------- #

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health(service: ServiceDep) -> HealthResponse:
        return HealthResponse(
            config_version=service.ctx.config.config_version,
            brands=len(service.brands()),
            listings=len(service.repo.listing_ids()),
            index_points=len(service.repo.list_index_points()),
            events=len(service.repo.list_events()),
            garments=len(service.repo.list_garments()),
        )

    # -- FR-2 listings -------------------------------------------------------- #

    @app.post("/listings/load", response_model=ListingsLoadResponse, tags=["listings"])
    def load_listings(body: ListingsLoadRequest, service: ServiceDep) -> ListingsLoadResponse:
        report = service.load_listings(source=body.source, path=body.path)
        payload = report.model_dump()
        # The full unresolved list can run to thousands of rows; the counts are the
        # contract, a sample is the diagnostic.
        payload["unresolved_refs"] = list(report.unresolved_refs[:20])
        return ListingsLoadResponse(**payload)

    @app.get("/listings", response_model=list[Listing], tags=["listings"])
    def list_listings(
        service: ServiceDep,
        stratum: str | None = None,
        status_filter: Annotated[ListingStatus | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    ) -> list[Listing]:
        return service.list_listings(stratum=stratum, status=status_filter, limit=limit)

    @app.get("/listings/{listing_id}", response_model=Listing, tags=["listings"])
    def get_listing(listing_id: str, service: ServiceDep) -> Listing:
        return service.get_listing(listing_id)

    # -- FR-4 index ------------------------------------------------------------ #

    @app.post("/index/build", response_model=IndexBuildResponse, tags=["index"])
    def build_index(body: IndexBuildRequest, service: ServiceDep) -> IndexBuildResponse:
        report = service.build_index(as_of=body.as_of)
        return IndexBuildResponse(
            as_of=report.as_of,
            strata_built=report.strata_built,
            leaf_strata=report.leaf_strata,
            points_written=report.points_written,
            excluded_total=report.excluded_total,
        )

    @app.get("/index/{stratum_id:path}", response_model=IndexSeriesResponse, tags=["index"])
    def index_series(
        stratum_id: str,
        service: ServiceDep,
        from_week: Annotated[str | None, Query(alias="from")] = None,
        to_week: Annotated[str | None, Query(alias="to")] = None,
        weeks: Annotated[int | None, Query(ge=1)] = None,
        excluded: bool = False,
    ) -> IndexSeriesResponse:
        points = service.index_points(stratum_id, from_week=from_week, to_week=to_week, weeks=weeks)
        removed: dict[str, list[Listing]] = {}
        if excluded:
            for point in points:
                if point.n_excluded:
                    removed[point.week] = service.excluded_listings(stratum_id, point.week)
        return IndexSeriesResponse(stratum_id=stratum_id, points=points, excluded=removed)

    @app.get("/strata", response_model=StrataResponse, tags=["index"])
    def strata(
        service: ServiceDep,
        level: Annotated[str | None, Query(pattern="^(leaf|era|brand)$")] = None,
        brand: str | None = None,
    ) -> StrataResponse:
        return StrataResponse(strata=service.strata(level=level, brand=brand))

    # -- FR-5 events ----------------------------------------------------------- #

    @app.post("/events/ingest", response_model=EventIngestResponse, tags=["events"])
    def ingest_events(body: EventIngestRequest, service: ServiceDep) -> EventIngestResponse:
        report = service.ingest_event_feed(
            source=body.source,
            path=body.path,
            since=body.since,
            until=body.until,
            social=body.social,
        )
        return EventIngestResponse(
            created=report.created,
            corroborated=report.corroborated,
            unchanged=report.unchanged,
            by_status={key.value: value for key, value in report.by_status.items()},
        )

    @app.post("/events", response_model=FashionEvent, tags=["events"])
    def create_event(
        body: EventCreateRequest, service: ServiceDep, response: Response
    ) -> FashionEvent:
        event, created = service.add_event(
            event_type=body.event_type,
            brand=body.brand,
            occurred_on=body.occurred_on,
            era=body.era,
            source=body.source,
            source_ref=body.source_ref,
            attributes=body.attributes,
            notes=body.notes,
        )
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return event

    @app.post("/events/{event_id}/confirm", response_model=FashionEvent, tags=["events"])
    def confirm_event(event_id: str, service: ServiceDep) -> FashionEvent:
        return service.set_event_status(event_id, EventStatus.CONFIRMED)

    @app.post("/events/{event_id}/reject", response_model=FashionEvent, tags=["events"])
    def reject_event(event_id: str, service: ServiceDep) -> FashionEvent:
        return service.set_event_status(event_id, EventStatus.REJECTED)

    @app.get("/events", response_model=list[FashionEvent], tags=["events"])
    def list_events(
        service: ServiceDep,
        type_filter: Annotated[EventType | None, Query(alias="type")] = None,
        brand: str | None = None,
        status_filter: Annotated[EventStatus | None, Query(alias="status")] = None,
        since: str | None = None,
    ) -> list[FashionEvent]:
        return service.list_events(
            event_type=type_filter, brand=brand, status=status_filter, since=since
        )

    @app.get("/events/{event_id}", response_model=EventDetailResponse, tags=["events"])
    def get_event(event_id: str, service: ServiceDep) -> EventDetailResponse:
        detail = service.event_detail(event_id)
        return EventDetailResponse(
            event=detail.event,
            targets=[dict(target) for target in detail.targets],
            priors=[dict(prior) for prior in detail.priors],
            retirement_age_weeks=detail.retirement_age_weeks,
            source_refs=list(detail.source_refs),
            corroboration=detail.corroboration,
        )

    # -- gazetteer -------------------------------------------------------------- #

    @app.get("/brands", response_model=list[Brand], tags=["gazetteer"])
    def list_brands(service: ServiceDep) -> list[Brand]:
        return service.brands()

    @app.get("/brands/{brand_id}", response_model=Brand, tags=["gazetteer"])
    def get_brand(brand_id: str, service: ServiceDep) -> Brand:
        return service.brand(brand_id)

    # -- FR-11 portfolio --------------------------------------------------------- #

    @app.post(
        "/portfolio",
        response_model=Garment,
        status_code=status.HTTP_201_CREATED,
        tags=["portfolio"],
    )
    def add_garment(body: GarmentCreateRequest, service: ServiceDep) -> Garment:
        return service.add_garment(**body.model_dump())

    @app.get("/portfolio", response_model=GarmentListResponse, tags=["portfolio"])
    def list_portfolio(service: ServiceDep, include_deleted: bool = False) -> GarmentListResponse:
        return GarmentListResponse(garments=service.list_garments(include_deleted=include_deleted))

    @app.get("/portfolio/value", response_model=PortfolioValuationResponse, tags=["portfolio"])
    def value_portfolio(
        service: ServiceDep, as_of: str | None = None
    ) -> PortfolioValuationResponse:
        rows = service.value_portfolio(as_of=as_of)
        week = week_key(as_of) if as_of else service._latest_index_week(service.index_view())
        values = [_valuation(garment, result, week) for garment, result in rows]
        return PortfolioValuationResponse(
            as_of_week=week,
            total_fair_value=sum(row.fair_value or 0.0 for row in values),
            total_anchor_price=sum(row.anchor_price or 0.0 for row in values),
            unavailable=sum(
                1 for row in values if row.valuation_method is ValuationMethod.UNAVAILABLE
            ),
            garments=values,
        )

    @app.get("/portfolio/{garment_id}", response_model=Garment, tags=["portfolio"])
    def get_garment(garment_id: str, service: ServiceDep) -> Garment:
        return service.get_garment(garment_id)

    @app.patch("/portfolio/{garment_id}", response_model=Garment, tags=["portfolio"])
    def patch_garment(garment_id: str, body: GarmentPatchRequest, service: ServiceDep) -> Garment:
        return service.edit_garment(garment_id, **body.model_dump(exclude_none=True))

    @app.delete("/portfolio/{garment_id}", response_model=Garment, tags=["portfolio"])
    def delete_garment(garment_id: str, service: ServiceDep) -> Garment:
        """Soft delete (FR-11): historical advice stays readable."""
        return service.remove_garment(garment_id)

    @app.get(
        "/portfolio/{garment_id}/valuation", response_model=ValuationResponse, tags=["portfolio"]
    )
    def garment_valuation(
        garment_id: str, service: ServiceDep, as_of: str | None = None
    ) -> ValuationResponse:
        garment = service.get_garment(garment_id)
        result = service.value_one(garment, as_of=as_of)
        week = week_key(as_of) if as_of else service._latest_index_week(service.index_view())
        return _valuation(garment, result, week)

    # -- FR-8 advice -------------------------------------------------------------- #

    @app.post("/advise", response_model=AdviceBatchResponse, tags=["advice"])
    def advise(body: AdviseRequest, service: ServiceDep) -> AdviceBatchResponse:
        rows = service.advise(as_of=body.as_of)
        counts: dict[AdviceAction, int] = {action: 0 for action in AdviceAction}
        for advice in rows:
            counts[advice.action] += 1
        return AdviceBatchResponse(
            as_of_week=rows[0].as_of_week if rows else "",
            counts=counts,
            advice=rows,
        )

    @app.get("/advice", response_model=list[Advice], tags=["advice"])
    def list_advice(
        service: ServiceDep,
        garment: str | None = None,
        action: AdviceAction | None = None,
        as_of: str | None = None,
        history: bool = False,
    ) -> list[Advice]:
        return service.list_advice(garment=garment, action=action, as_of=as_of, history=history)

    @app.get("/advice/{advice_id}", response_model=Advice, tags=["advice"])
    def get_advice(advice_id: str, service: ServiceDep) -> Advice:
        return service.get_advice(advice_id)

    # -- FR-10 backtests ------------------------------------------------------------ #

    @app.post(
        "/backtests",
        response_model=BacktestRunResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["backtests"],
    )
    def create_backtest(body: BacktestRequest, service: ServiceDep) -> BacktestRunResponse:
        run, results = service.backtest(
            start=body.start,
            end=body.end,
            placebo_seed=body.placebo_seed,
            reference=body.reference,
            scenario=body.scenario,
        )
        return BacktestRunResponse(
            id=run.id,
            params=run.params.model_dump(),
            as_of=run.as_of,
            aggregates=run.aggregates,
            n_results=len(results),
        )

    @app.get("/backtests", response_model=list[BacktestRunResponse], tags=["backtests"])
    def list_backtests(
        service: ServiceDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> list[BacktestRunResponse]:
        return [
            BacktestRunResponse(
                id=run.id,
                params=run.params.model_dump(),
                as_of=run.as_of,
                aggregates=run.aggregates,
                n_results=len(service.repo.list_backtest_results(run.id)),
            )
            for run in service.list_backtests(limit=limit)
        ]

    @app.get("/backtests/{run_id}", response_model=BacktestRunResponse, tags=["backtests"])
    def get_backtest(run_id: str, service: ServiceDep) -> BacktestRunResponse:
        run, results = service.get_backtest(run_id)
        return BacktestRunResponse(
            id=run.id,
            params=run.params.model_dump(),
            as_of=run.as_of,
            aggregates=run.aggregates,
            n_results=len(results),
        )

    return app


def _valuation(garment: Garment, result: Any, week: str) -> ValuationResponse:
    return ValuationResponse(
        garment_id=garment.id,
        as_of_week=week,
        valuation_method=result.method,
        fair_value=result.fair_value,
        reason=result.reason,
        stratum_id=result.stratum_id,
        level_usd=result.level_usd,
        anchor_price=garment.anchor_price,
        unrealized_gain=result.unrealized_gain,
    )


app = create_app()
