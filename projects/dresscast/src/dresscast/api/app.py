"""The FastAPI surface (SCOPE.md FR-17, §Architecture-API).

Thin by construction: every handler parses its request, calls exactly one
:class:`~dresscast.services.DresscastService` method and serializes the result.
No business rule lives here — the error catalog below is a transport mapping of
:class:`~dresscast.errors.DresscastError` codes, not a second rulebook.

The clock is read *at this edge* and passed down (FR-19): the engine never
reads it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.responses import JSONResponse

from dresscast import __version__
from dresscast.api.schemas import (
    ErrorResponse,
    GarmentCreate,
    GarmentPatch,
    Health,
    LaundryRequest,
    PhotoAttach,
    RecommendationRequest,
    SuggestionAccept,
    WearFromRecommendation,
    WearRequest,
)
from dresscast.engine.models import (
    ENGINE_VERSION,
    SCHEMA_VERSION,
    AttributeSuggestion,
    DayBrief,
    DayForecast,
    Garment,
    LaundryEvent,
    Recommendation,
    WearLog,
)
from dresscast.errors import DresscastError
from dresscast.services import DresscastService, build_service, parse_window

#: FR-17's error catalog → HTTP status.  Anything unlisted is a 500.
STATUS_BY_CODE: dict[str, int] = {
    "invalid_params": 400,
    "unknown_garment": 404,
    "invalid_transition": 409,
    "infeasible_wardrobe": 422,
    "wardrobe_too_large": 413,
    "no_extractor_configured": 501,
    "forecast_unavailable": 503,
    "invalid_schema_version": 500,
}

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "invalid_params"},
    404: {"model": ErrorResponse, "description": "unknown_garment"},
    409: {"model": ErrorResponse, "description": "invalid_transition"},
    422: {"model": ErrorResponse, "description": "infeasible_wardrobe (carries the day brief)"},
}


def _colors(values: list[Any] | None) -> list[Any] | None:
    if values is None:
        return None
    return [v if isinstance(v, str) else v.model_dump(exclude_none=True) for v in values]


def default_service() -> Iterator[DresscastService]:
    """The un-overridden dependency: one SQLite-backed service per request."""
    made = build_service()
    try:
        yield made
    finally:
        made.close()


#: Handlers depend on this alias; :func:`create_app` overrides the provider.
Svc = Annotated[DresscastService, Depends(default_service)]


def create_app(
    *,
    service: DresscastService | None = None,
    service_factory: Callable[[], DresscastService] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the app.

    ``service`` pins one long-lived service (the ``dresscast serve`` path and
    tests); ``service_factory`` opens one per request; neither means "open the
    default SQLite database per request".
    """
    now_fn = clock or (lambda: datetime.now(timezone.utc))

    app = FastAPI(
        title="dresscast",
        version=__version__,
        summary="Weather-aware outfit assembly from your photographed wardrobe.",
    )

    if service is not None:

        def _pinned() -> Iterator[DresscastService]:
            yield service  # type: ignore[misc]

        app.dependency_overrides[default_service] = _pinned
    elif service_factory is not None:

        def _per_request() -> Iterator[DresscastService]:
            made = service_factory()  # type: ignore[misc]
            try:
                yield made
            finally:
                made.close()

        app.dependency_overrides[default_service] = _per_request

    @app.exception_handler(DresscastError)
    async def _domain_error(_: Request, exc: DresscastError) -> JSONResponse:
        code = STATUS_BY_CODE.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        return JSONResponse(status_code=code, content={"error": exc.as_dict()})

    # -- health ------------------------------------------------------------

    @app.get("/health", response_model=Health, tags=["meta"])
    def health() -> Health:
        return Health(
            status="ok",
            version=__version__,
            engine_version=ENGINE_VERSION,
            schema_version=SCHEMA_VERSION,
        )

    # -- garments (FR-1, FR-3) --------------------------------------------

    @app.post(
        "/garments",
        response_model=Garment,
        status_code=status.HTTP_201_CREATED,
        responses=ERROR_RESPONSES,
        tags=["garments"],
    )
    def create_garment(body: GarmentCreate, svc: Svc) -> Garment:
        return svc.create_garment(
            name=body.name,
            category=body.category,
            colors=_colors(body.colors) or [],
            occasions=body.occasions,
            style_tags=body.style_tags,
            clo=body.clo,
            warmth=body.warmth,
            layer_role=body.layer_role,
            accessory_class=body.accessory_class,
            formality=body.formality,
            waterproofness=body.waterproofness,
            windproofness=body.windproofness,
            wears_before_laundry=body.wears_before_laundry,
            notes=body.notes,
            now=now_fn(),
        )

    @app.get("/garments", response_model=list[Garment], tags=["garments"])
    def list_garments(
        svc: Svc,
        status_filter: Annotated[str | None, Query(alias="status")] = None,
        occasion: str | None = None,
        category: str | None = None,
    ) -> list[Garment]:
        return svc.list_garments(
            status=status_filter, occasion=occasion, category=category
        )

    @app.get(
        "/garments/{garment_id}",
        response_model=Garment,
        responses=ERROR_RESPONSES,
        tags=["garments"],
    )
    def get_garment(garment_id: str, svc: Svc) -> Garment:
        return svc.resolve_garment(garment_id)

    @app.patch(
        "/garments/{garment_id}",
        response_model=Garment,
        responses=ERROR_RESPONSES,
        tags=["garments"],
    )
    def patch_garment(garment_id: str, body: GarmentPatch, svc: Svc) -> Garment:
        fields = body.model_dump(exclude_none=True)
        if "colors" in fields:
            fields["colors"] = _colors(body.colors)
        return svc.edit_garment(garment_id, now=now_fn(), **fields)

    @app.post(
        "/garments/{garment_id}/photo",
        response_model=Garment,
        responses=ERROR_RESPONSES,
        tags=["garments"],
    )
    def attach_photo(garment_id: str, body: PhotoAttach, svc: Svc) -> Garment:
        return svc.attach_photo(garment_id, body.path, now=now_fn())

    # -- suggestions (FR-2) ------------------------------------------------

    @app.post(
        "/garments/{garment_id}/suggest",
        response_model=AttributeSuggestion,
        status_code=status.HTTP_201_CREATED,
        responses={**ERROR_RESPONSES, 501: {"model": ErrorResponse}},
        tags=["suggestions"],
    )
    def suggest(garment_id: str, svc: Svc) -> AttributeSuggestion:
        return svc.suggest(garment_id, now=now_fn())

    @app.get(
        "/garments/{garment_id}/suggestions",
        response_model=list[AttributeSuggestion],
        responses=ERROR_RESPONSES,
        tags=["suggestions"],
    )
    def list_suggestions(garment_id: str, svc: Svc) -> list[AttributeSuggestion]:
        svc.resolve_garment(garment_id)
        return svc.list_suggestions(garment_id)

    @app.post(
        "/suggestions/{suggestion_id}/accept",
        response_model=Garment,
        responses=ERROR_RESPONSES,
        tags=["suggestions"],
    )
    def accept_suggestion(suggestion_id: str, body: SuggestionAccept, svc: Svc) -> Garment:
        _, garment = svc.accept_suggestion(suggestion_id, body.fields, now=now_fn())
        return garment

    @app.post(
        "/suggestions/{suggestion_id}/reject",
        response_model=AttributeSuggestion,
        responses=ERROR_RESPONSES,
        tags=["suggestions"],
    )
    def reject_suggestion(suggestion_id: str, svc: Svc) -> AttributeSuggestion:
        return svc.reject_suggestion(suggestion_id, now=now_fn())

    # -- forecast and brief (FR-4, FR-16) ----------------------------------

    @app.get(
        "/forecast",
        response_model=DayForecast,
        responses={**ERROR_RESPONSES, 503: {"model": ErrorResponse}},
        tags=["weather"],
    )
    def forecast(svc: Svc, date: str) -> DayForecast:
        return svc.ensure_forecast(date, now=now_fn())

    @app.get(
        "/brief",
        response_model=DayBrief,
        responses={**ERROR_RESPONSES, 503: {"model": ErrorResponse}},
        tags=["weather"],
    )
    def brief(
        svc: Svc,
        date: str,
        met: float | None = None,
        window: str | None = None,
    ) -> DayBrief:
        return svc.brief(
            date=date,
            met=met,
            wear_window=parse_window(window) if window else None,
            now=now_fn(),
        )

    # -- recommendations (FR-8, FR-13) -------------------------------------

    @app.post(
        "/recommendations",
        response_model=Recommendation,
        status_code=status.HTTP_201_CREATED,
        responses=ERROR_RESPONSES,
        tags=["recommendations"],
    )
    def create_recommendation(body: RecommendationRequest, svc: Svc) -> Recommendation:
        return svc.recommend(
            date=body.date,
            occasion=body.occasion,
            wear_window=body.wear_window,
            commute_hours=body.commute_hours,
            met=body.met,
            k=body.k,
            seed=body.seed,
            now=now_fn(),
        )

    @app.get(
        "/recommendations/{recommendation_id}",
        response_model=Recommendation,
        responses=ERROR_RESPONSES,
        tags=["recommendations"],
    )
    def get_recommendation(recommendation_id: str, svc: Svc) -> Recommendation:
        return svc.resolve_recommendation(recommendation_id)

    # -- wear and laundry (FR-12, FR-3) ------------------------------------

    @app.post(
        "/recommendations/{recommendation_id}/wear",
        response_model=WearLog,
        status_code=status.HTTP_201_CREATED,
        responses=ERROR_RESPONSES,
        tags=["wear"],
    )
    def wear_recommendation(
        recommendation_id: str, body: WearFromRecommendation, svc: Svc
    ) -> WearLog:
        return svc.wear_recommendation(
            recommendation_id, rank=body.rank, date=body.date, now=now_fn()
        )

    @app.post(
        "/wear",
        response_model=WearLog,
        status_code=status.HTTP_201_CREATED,
        responses=ERROR_RESPONSES,
        tags=["wear"],
    )
    def wear(body: WearRequest, svc: Svc) -> WearLog:
        return svc.wear_items(body.garment_ids, date=body.date, now=now_fn())

    @app.delete(
        "/wear/{log_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=ERROR_RESPONSES,
        tags=["wear"],
    )
    def undo_wear(log_id: str, svc: Svc) -> Response:
        svc.undo_wear(log_id, now=now_fn())
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/laundry",
        response_model=LaundryEvent,
        status_code=status.HTTP_201_CREATED,
        responses=ERROR_RESPONSES,
        tags=["laundry"],
    )
    def laundry(body: LaundryRequest, svc: Svc) -> LaundryEvent:
        return svc.launder(
            body.garment_ids, all_dirty=body.all_dirty, note=body.note, now=now_fn()
        )

    return app


app = create_app()
"""Module-level ASGI app for ``uvicorn dresscast.api:app``."""
