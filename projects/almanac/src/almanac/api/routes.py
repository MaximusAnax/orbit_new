"""FR-15: the FastAPI application.

Thin by construction — every handler parses, delegates to
:class:`~almanac.service.AlmanacService`, and serializes.  No business rule
lives in this module; the only logic here is the error catalog that maps
service and store exceptions onto status codes.

Error catalog
-------------

===================================  ======  ==========================================
condition                            status  ``error.code``
===================================  ======  ==========================================
unknown entity                       404     ``not_found``
day not materialized (GET /today)    404     ``not_materialized``
reflection exists / superseded       409     ``conflict``
duplicate name or id                 409     ``duplicate``
no candidate card for a draw         409     ``no_candidate``
invariant violation (FR-4, FR-8...)  422     ``unprocessable``
surfacing out of order (FR-8)        422     ``out_of_order``
malformed request body/query         422     ``validation_error``
===================================  ======  ==========================================
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from almanac import __version__
from almanac.api.schemas import (
    CollectionPatch,
    CollectionRequest,
    CollectionView,
    CreateEntryRequest,
    DrawRequest,
    EntryListResponse,
    ErrorBody,
    ErrorResponse,
    HealthResponse,
    ImportRequest,
    PatchEntryRequest,
    PinResponse,
    ReflectionRequest,
    SuggestRequest,
    SuggestResponse,
    TemplateSummary,
    TodayRequest,
)
from almanac.factory import open_service, starter_pack
from almanac.models import (
    AttributionFinding,
    CaptureResult,
    Card,
    Entry,
    EntryDetail,
    EntryKind,
    EntryStatus,
    ImportReport,
    LibraryExport,
    Reflection,
    StatsReport,
    Surfacing,
    Theme,
)
from almanac.service import (
    AlmanacService,
    Conflict,
    NotFound,
    ServiceError,
    ValidationFailed,
)
from almanac.store.repository import DuplicateError, MonotonicityError

ServiceFactory = Callable[[], AlmanacService]

#: Starlette renamed its 422 constant; the integer keeps the module warning-free.
HTTP_422 = 422

_ERRORS: dict[int, dict] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump(),
    )


class DayResponse(BaseModel):
    """The card set for one date (FR-8). ``materialized_now`` is false on re-reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    on_date: dt.date
    materialized_now: bool
    cards: list[Card]


class NotMaterialized(ServiceError):
    """GET /today for a date whose cards were never materialized (404)."""


class NoCandidate(ServiceError):
    """A draw whose filtered candidate set is empty (409)."""


#: The listing status filter, including the "everything" option (FR-12).
_STATUS_FILTER: dict[str, EntryStatus | None] = {
    "active": EntryStatus.ACTIVE,
    "archived": EntryStatus.ARCHIVED,
    "all": None,
}


def create_app(service_factory: ServiceFactory | None = None) -> FastAPI:
    """Build the app. ``service_factory`` is overridden by tests and evals."""
    app = FastAPI(
        title="Almanac",
        version=__version__,
        summary="A personal quote-and-idea almanac with a deterministic daily scheduler.",
    )
    factory: ServiceFactory = service_factory or open_service

    def get_service() -> AlmanacService:
        return factory()

    Svc = Depends(get_service)

    # --- error catalog ----------------------------------------------------
    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, "not_found", str(exc))

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "conflict", str(exc))

    @app.exception_handler(ValidationFailed)
    async def _unprocessable(_: Request, exc: ValidationFailed) -> JSONResponse:
        return _error(HTTP_422, "unprocessable", str(exc))

    @app.exception_handler(MonotonicityError)
    async def _out_of_order(_: Request, exc: MonotonicityError) -> JSONResponse:
        return _error(HTTP_422, "out_of_order", str(exc))

    @app.exception_handler(DuplicateError)
    async def _duplicate(_: Request, exc: DuplicateError) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "duplicate", str(exc))

    @app.exception_handler(NotMaterialized)
    async def _not_materialized(_: Request, exc: NotMaterialized) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, "not_materialized", str(exc))

    @app.exception_handler(NoCandidate)
    async def _no_candidate(_: Request, exc: NoCandidate) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "no_candidate", str(exc))

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return _error(HTTP_422, "unprocessable", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
        return _error(
            HTTP_422,
            "validation_error",
            f"{where}: {first.get('msg', 'invalid request')}",
        )

    # --- health -----------------------------------------------------------
    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health(service: AlmanacService = Svc) -> HealthResponse:
        return HealthResponse(
            version=__version__,
            scheduler_version=service.params.params_version,
            today=service.clock.today(),
            seed=service.seed,
            batch_k=service.batch_k,
        )

    # --- entries ----------------------------------------------------------
    @app.post(
        "/entries",
        response_model=CaptureResult,
        status_code=status.HTTP_201_CREATED,
        responses=_ERRORS,
        tags=["entries"],
    )
    def create_entry(body: CreateEntryRequest, service: AlmanacService = Svc) -> CaptureResult:
        """FR-1/2/3: create, warn on duplicates, suggest themes, flag attribution."""
        return service.capture(
            text=body.text,
            kind=body.kind,
            author=body.author,
            source=body.source,
            url=body.url,
            note=body.note,
            tags=body.tags,
            themes=body.themes,
            captured_on=body.captured_on,
            accept_suggestions=body.accept_suggestions,
        )

    @app.get("/entries", response_model=EntryListResponse, responses=_ERRORS, tags=["entries"])
    def list_entries(
        service: AlmanacService = Svc,
        status_filter: Literal["active", "archived", "all"] = Query(
            default="active", alias="status"
        ),
        pinned: bool | None = None,
        tag: str | None = None,
        theme: str | None = None,
        kind: EntryKind | None = None,
        q: str | None = None,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> EntryListResponse:
        """FR-12: status/pinned/tag/theme/kind filters compose with search."""
        matched = service.list_entries(
            status=_STATUS_FILTER[status_filter],
            pinned=pinned,
            kind=kind,
            tag=tag,
            theme=theme,
            query=q,
        )
        return EntryListResponse(total=len(matched), entries=matched[offset : offset + limit])

    @app.post("/entries/import", response_model=ImportReport, responses=_ERRORS, tags=["entries"])
    def import_entries(body: ImportRequest, service: AlmanacService = Svc) -> ImportReport:
        """FR-5: json | csv | starter | rows, with the capacity-derived horizon."""
        if body.format == "starter":
            return service.import_starter(starter_pack())
        if body.format == "rows":
            return service.import_candidates(body.rows or [])
        if body.payload is None:
            raise ValidationFailed(f"format={body.format} requires a payload")
        if body.format == "json":
            return service.import_json(body.payload)
        return service.import_csv(body.payload)

    @app.get("/entries/export", response_model=LibraryExport, tags=["entries"])
    def export_library(service: AlmanacService = Svc) -> LibraryExport:
        """FR-5: the whole library as one JSON document."""
        return service.export_library()

    @app.get("/entries/{entry_id}", response_model=EntryDetail, responses=_ERRORS, tags=["entries"])
    def get_entry(entry_id: str, service: AlmanacService = Svc) -> EntryDetail:
        """The entry plus its scheduler state and full history."""
        return service.entry_detail(entry_id)

    @app.patch("/entries/{entry_id}", response_model=Entry, responses=_ERRORS, tags=["entries"])
    def patch_entry(entry_id: str, body: PatchEntryRequest, service: AlmanacService = Svc) -> Entry:
        """FR-4: a post-surfacing text edit is 422 unless the hash is unchanged."""
        return service.edit_entry(
            entry_id,
            text=body.text,
            author=body.author,
            source=body.source,
            url=body.url,
            note=body.note,
            tags=body.tags,
            themes=body.themes,
        )

    @app.post(
        "/entries/{entry_id}/pin", response_model=PinResponse, responses=_ERRORS, tags=["entries"]
    )
    def pin_entry(entry_id: str, service: AlmanacService = Svc) -> PinResponse:
        """FR-4: pinning past ``8 * k`` returns the degraded guarantee as a warning."""
        entry, warning = service.pin(entry_id)
        return PinResponse(entry=entry, warning=warning)

    @app.delete(
        "/entries/{entry_id}/pin", response_model=Entry, responses=_ERRORS, tags=["entries"]
    )
    def unpin_entry(entry_id: str, service: AlmanacService = Svc) -> Entry:
        return service.unpin(entry_id)

    @app.post(
        "/entries/{entry_id}/archive", response_model=Entry, responses=_ERRORS, tags=["entries"]
    )
    def archive_entry(entry_id: str, service: AlmanacService = Svc) -> Entry:
        return service.archive(entry_id)

    @app.post(
        "/entries/{entry_id}/restore", response_model=Entry, responses=_ERRORS, tags=["entries"]
    )
    def restore_entry(entry_id: str, service: AlmanacService = Svc) -> Entry:
        return service.restore(entry_id)

    # --- daily card and draws --------------------------------------------
    @app.post("/today", response_model=DayResponse, responses=_ERRORS, tags=["cards"])
    def materialize_today(
        body: TodayRequest | None = None, service: AlmanacService = Svc
    ) -> DayResponse:
        """FR-8: materialize the day's cards at most once; re-reads are identical."""
        on = (body.date if body else None) or service.clock.today()
        existing = service.get_day(on)
        cards = existing or service.materialize_day(on)
        return DayResponse(on_date=on, materialized_now=not existing, cards=cards)

    @app.get("/today", response_model=DayResponse, responses=_ERRORS, tags=["cards"])
    def read_today(service: AlmanacService = Svc, date: dt.date | None = None) -> DayResponse:
        """Read-only: 404 when the date has not been materialized."""
        on = date or service.clock.today()
        cards = service.get_day(on)
        if not cards:
            raise NotMaterialized(f"no cards have been materialized for {on}")
        return DayResponse(on_date=on, materialized_now=False, cards=cards)

    @app.post(
        "/draws",
        response_model=Card,
        status_code=status.HTTP_201_CREATED,
        responses=_ERRORS,
        tags=["cards"],
    )
    def draw(body: DrawRequest | None = None, service: AlmanacService = Svc) -> Card:
        """FR-8/FR-13: an extra card, optionally filtered by theme or collection."""
        request = body or DrawRequest()
        card = service.draw(
            on_date=request.date,
            theme_id=request.theme,
            collection_id=request.collection_id,
        )
        if card is None:
            raise NoCandidate("no eligible entry matches that draw")
        return card

    # --- surfacings and reflections --------------------------------------
    @app.get("/surfacings", response_model=list[Surfacing], tags=["history"])
    def list_surfacings(
        service: AlmanacService = Svc,
        entry_id: str | None = None,
        date_from: dt.date | None = Query(default=None, alias="from"),
        date_to: dt.date | None = Query(default=None, alias="to"),
    ) -> list[Surfacing]:
        return service.repo.list_surfacings(entry_id=entry_id, date_from=date_from, date_to=date_to)

    @app.get(
        "/surfacings/{surfacing_id}",
        response_model=Surfacing,
        responses=_ERRORS,
        tags=["history"],
    )
    def get_surfacing(surfacing_id: str, service: AlmanacService = Svc) -> Surfacing:
        row = service.repo.get_surfacing(surfacing_id)
        if row is None:
            raise NotFound(f"unknown surfacing {surfacing_id}")
        return row

    @app.post(
        "/surfacings/{surfacing_id}/reflection",
        response_model=Reflection,
        status_code=status.HTTP_201_CREATED,
        responses=_ERRORS,
        tags=["history"],
    )
    def add_reflection(
        surfacing_id: str, body: ReflectionRequest, service: AlmanacService = Svc
    ) -> Reflection:
        """FR-11: one immutable reflection per surfacing; 409 once superseded."""
        return service.reflect(surfacing_id, body.grade, body.text)

    @app.get("/reflections", response_model=list[Reflection], tags=["history"])
    def list_reflections(
        service: AlmanacService = Svc,
        entry_id: str | None = None,
        date_from: dt.date | None = Query(default=None, alias="from"),
        date_to: dt.date | None = Query(default=None, alias="to"),
    ) -> list[Reflection]:
        return service.repo.list_reflections(
            entry_id=entry_id, date_from=date_from, date_to=date_to
        )

    # --- taxonomy ---------------------------------------------------------
    @app.get("/themes", response_model=list[Theme], tags=["taxonomy"])
    def list_themes(service: AlmanacService = Svc) -> list[Theme]:
        return service.datasets.themes

    @app.get(
        "/themes/{theme_id}/templates",
        response_model=list[TemplateSummary],
        responses=_ERRORS,
        tags=["taxonomy"],
    )
    def list_templates(theme_id: str, service: AlmanacService = Svc) -> list[TemplateSummary]:
        pool = [t for t in service.datasets.templates if t.theme_id == theme_id]
        if not pool:
            raise NotFound(f"unknown theme {theme_id}")
        return [
            TemplateSummary(id=t.id, theme_id=t.theme_id, kind=t.kind.value, template=t.template)
            for t in pool
        ]

    @app.post("/themes/suggest", response_model=SuggestResponse, tags=["taxonomy"])
    def suggest_themes(body: SuggestRequest, service: AlmanacService = Svc) -> SuggestResponse:
        """FR-3: the deterministic lexicon suggester, top 3."""
        return SuggestResponse(suggestions=service.suggest(body.text, body.tags, body.note))

    @app.get("/tags", response_model=list[str], tags=["taxonomy"])
    def list_tags(service: AlmanacService = Svc) -> list[str]:
        return [tag.name for tag in service.repo.list_tags()]

    # --- collections ------------------------------------------------------
    def _collection_view(collection_id: str, service: AlmanacService) -> CollectionView:
        row = service.repo.get_collection(collection_id)
        if row is None:
            raise NotFound(f"unknown collection {collection_id}")
        return CollectionView(
            id=row.id,
            name=row.name,
            description=row.description,
            created_at=row.created_at,
            entry_ids=service.repo.collection_entry_ids(row.id),
        )

    @app.post(
        "/collections",
        response_model=CollectionView,
        status_code=status.HTTP_201_CREATED,
        responses=_ERRORS,
        tags=["collections"],
    )
    def create_collection(body: CollectionRequest, service: AlmanacService = Svc) -> CollectionView:
        created = service.create_collection(body.name, body.description)
        return _collection_view(created.id, service)

    @app.get("/collections", response_model=list[CollectionView], tags=["collections"])
    def list_collections(service: AlmanacService = Svc) -> list[CollectionView]:
        return [_collection_view(c.id, service) for c in service.repo.list_collections()]

    @app.get(
        "/collections/{collection_id}",
        response_model=CollectionView,
        responses=_ERRORS,
        tags=["collections"],
    )
    def get_collection(collection_id: str, service: AlmanacService = Svc) -> CollectionView:
        return _collection_view(collection_id, service)

    @app.patch(
        "/collections/{collection_id}",
        response_model=CollectionView,
        responses=_ERRORS,
        tags=["collections"],
    )
    def patch_collection(
        collection_id: str, body: CollectionPatch, service: AlmanacService = Svc
    ) -> CollectionView:
        current = service.repo.get_collection(collection_id)
        if current is None:
            raise NotFound(f"unknown collection {collection_id}")
        updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        service.repo.update_collection(current.model_copy(update=updates))
        return _collection_view(collection_id, service)

    @app.delete(
        "/collections/{collection_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_ERRORS,
        tags=["collections"],
    )
    def delete_collection(collection_id: str, service: AlmanacService = Svc) -> Response:
        if service.repo.get_collection(collection_id) is None:
            raise NotFound(f"unknown collection {collection_id}")
        service.repo.delete_collection(collection_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.put(
        "/collections/{collection_id}/entries/{entry_id}",
        response_model=CollectionView,
        responses=_ERRORS,
        tags=["collections"],
    )
    def add_collection_entry(
        collection_id: str, entry_id: str, service: AlmanacService = Svc
    ) -> CollectionView:
        service.add_to_collection(collection_id, entry_id)
        return _collection_view(collection_id, service)

    @app.delete(
        "/collections/{collection_id}/entries/{entry_id}",
        response_model=CollectionView,
        responses=_ERRORS,
        tags=["collections"],
    )
    def remove_collection_entry(
        collection_id: str, entry_id: str, service: AlmanacService = Svc
    ) -> CollectionView:
        if service.repo.get_collection(collection_id) is None:
            raise NotFound(f"unknown collection {collection_id}")
        service.remove_from_collection(collection_id, entry_id)
        return _collection_view(collection_id, service)

    # --- stats and attribution -------------------------------------------
    @app.get("/stats", response_model=StatsReport, tags=["meta"])
    def stats(service: AlmanacService = Svc, date: dt.date | None = None) -> StatsReport:
        """FR-14, including the capacity block and the ``lambda > 3`` advisory."""
        return service.stats(date)

    @app.get("/attribution/check", response_model=list[AttributionFinding], tags=["meta"])
    def check_attribution(
        service: AlmanacService = Svc,
        text: str = Query(min_length=1),
        author: str | None = None,
    ) -> list[AttributionFinding]:
        """FR-2 on demand. Findings never block anything."""
        return service.check_attribution(text, author)

    return app
