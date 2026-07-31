"""FastAPI application (FR-13).

Thin by construction: each handler parses its request, calls one
:mod:`flowlist.services` function, and serialises the result.  Every domain
error carries a stable code (:mod:`flowlist.errors`); :data:`STATUS_BY_CODE`
is the whole error contract, so adding a rule never means touching a handler.

Timestamps enter the system here — the process boundary is the only place
allowed to read a clock (CONVENTIONS 3).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from flowlist import ENGINE_VERSION, services
from flowlist.api.schemas import (
    AnalyzeRequest,
    EntryOut,
    FlowReportOut,
    HealthResponse,
    ImportIssueOut,
    ImportRequest,
    ImportResponse,
    ManualFeaturesRequest,
    PlaylistOut,
    PlaylistSummaryOut,
    ReorderRequest,
    RunEntryOut,
    RunOut,
    RunSummaryOut,
    ScoreRequest,
    TrackOut,
)
from flowlist.engine.keys import parse_key
from flowlist.engine.models import (
    DEFAULT_PRECEDENCE,
    CoverageReport,
    ExportFormat,
    FeatureSource,
    PlaylistSource,
    ReorderParams,
    Track,
    TransitionWeights,
)
from flowlist.engine.resolution import resolve_features
from flowlist.errors import (
    FlowlistError,
    InvalidProviderError,
    PlaylistImportError,
)
from flowlist.store.base import Repository
from flowlist.store.sqlite import SqliteRepository

#: FR-13's catalog plus the guards the engine raises on user input, mapped to
#: the status the HTTP surface returns.  Anything absent falls back to 400.
STATUS_BY_CODE: dict[str, int] = {
    "import_failed": 400,
    "unknown_playlist": 404,
    "unknown_track": 404,
    "unknown_run": 404,
    "name_conflict": 409,
    "playlist_has_runs": 409,
    "playlist_too_large": 413,
    "invalid_weights": 422,
    "invalid_anchor": 422,
    "invalid_providers": 422,
    "instance_too_large": 422,
    "adapter_unavailable": 503,
}

#: Export format -> (media type, filename suffix) for ``GET /runs/{id}/export``.
_EXPORT_MEDIA: dict[ExportFormat, tuple[str, str]] = {
    ExportFormat.M3U: ("audio/x-mpegurl", "m3u8"),
    ExportFormat.CSV: ("text/csv", "csv"),
    ExportFormat.JSON: ("application/json", "json"),
}


def _now() -> datetime:
    return datetime.now(UTC)


def get_repository(request: Request) -> Iterator[Repository]:
    """Repository dependency; tests override it with an in-memory backend.

    Holds the app-level lock for the whole request: FastAPI runs sync handlers
    on threadpool worker threads, and the shared SQLite connection must not
    interleave two requests' transactions (the connection itself is opened
    with ``check_same_thread=False`` — see ``store.sqlite``).  Whole-request
    serialization is the right granularity for a single-user local tool.
    """
    with request.app.state.repository_lock:
        yield request.app.state.repository


RepositoryDep = Annotated[Repository, Depends(get_repository)]


def _weights(raw: dict[str, float] | None) -> TransitionWeights:
    """Validate and normalize user weights (FR-6 -> ``invalid_weights``)."""
    return TransitionWeights.parse(raw).normalized()


def _precedence(names: list[str] | None) -> tuple[FeatureSource, ...]:
    """Turn ``providers: [manual, import]`` into a resolution precedence (FR-3)."""
    if not names:
        return DEFAULT_PRECEDENCE
    try:
        return tuple(FeatureSource(name.strip()) for name in names)
    except ValueError as exc:
        raise InvalidProviderError(
            f"unknown feature source(s) in providers: {names}",
            providers=names,
            allowed=[source.value for source in FeatureSource],
        ) from exc


def create_app(
    repository: Repository | None = None,
    *,
    db_path: str | None = None,
    catalog_path: str | None = None,
) -> FastAPI:
    """Build the app.

    ``repository`` is injected by tests and by ``flowlist serve --db``; when
    absent a SQLite repository at ``db_path`` is opened and closed with the
    application lifespan.
    """
    owned = repository is None
    store: Repository = repository or SqliteRepository(db_path or ":memory:")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned:  # pragma: no cover - exercised at process shutdown
            store.close()

    app = FastAPI(
        title="flowlist",
        version=ENGINE_VERSION,
        summary="Reorders a playlist so consecutive tracks transition seamlessly.",
        lifespan=lifespan,
    )
    app.state.repository = store
    app.state.repository_lock = threading.RLock()
    app.state.catalog_path = catalog_path

    @app.exception_handler(FlowlistError)
    async def _domain_error(_: Request, exc: FlowlistError) -> JSONResponse:
        return JSONResponse(status_code=STATUS_BY_CODE.get(exc.code, 400), content=exc.as_detail())

    _register_routes(app)
    return app


def _entry_views(view: services.PlaylistView) -> list[EntryOut]:
    return [
        EntryOut(
            entry_id=entry.id,
            position=entry.position,
            track=view.tracks[entry.track_id],
            features=view.features[entry.track_id].snapshot(),
            feature_sources={
                field: source.value
                for field, source in view.features[entry.track_id].field_sources.items()
            },
        )
        for entry in view.entries
    ]


def _playlist_out(view: services.PlaylistView) -> PlaylistOut:
    return PlaylistOut(
        **view.playlist.model_dump(),
        entries=len(view.entries),
        tracks=_entry_views(view),
        coverage=view.coverage,
    )


def _track_of(view: services.RunView, entry_id: str) -> Track | None:
    entry = view.entries.get(entry_id)
    return None if entry is None else view.tracks.get(entry.track_id)


def _run_out(view: services.RunView) -> RunOut:
    return RunOut(
        run=view.run,
        params=view.run.params,
        applied=view.applied,
        entries=[
            RunEntryOut(
                position=run_entry.position,
                entry_id=run_entry.entry_id,
                track=_track_of(view, run_entry.entry_id),
                transition=run_entry.transition,
            )
            for run_entry in view.run_entries
        ],
    )


def _register_routes(app: FastAPI) -> None:
    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(version=ENGINE_VERSION)

    # ------------------------------------------------------------ playlists
    @app.post(
        "/playlists/import",
        response_model=ImportResponse,
        status_code=201,
        tags=["playlists"],
    )
    def import_playlist(body: ImportRequest, repo: RepositoryDep) -> ImportResponse:
        """FR-1/FR-2 — import from inline content or a local path."""
        if (body.content is None) == (body.path is None):
            raise PlaylistImportError(
                "provide exactly one of 'content' (inline CSV/JSON) or 'path'"
            )
        if body.content is not None:
            imported = services.read_playlist_content(
                body.content,
                name=body.name,
                fmt=body.format or PlaylistSource.JSON,
            )
        else:
            assert body.path is not None
            imported = services.read_playlist(body.path, name=body.name, fmt=body.format)

        result = services.import_playlist(
            repo, imported, now=_now(), replace=body.replace, force=body.force
        )
        return ImportResponse(
            playlist=PlaylistSummaryOut.of(result.playlist, len(result.entries)),
            replaced=result.replaced,
            warnings=[ImportIssueOut(**issue.model_dump()) for issue in result.warnings],
            skipped=[ImportIssueOut(**issue.model_dump()) for issue in result.skipped],
        )

    @app.get("/playlists", response_model=list[PlaylistSummaryOut], tags=["playlists"])
    def list_playlists(repo: RepositoryDep) -> list[PlaylistSummaryOut]:
        return [
            PlaylistSummaryOut.of(playlist, len(repo.get_entries(playlist.id)))
            for playlist in repo.list_playlists()
        ]

    @app.get("/playlists/{playlist_id}", response_model=PlaylistOut, tags=["playlists"])
    def get_playlist(playlist_id: str, repo: RepositoryDep) -> PlaylistOut:
        return _playlist_out(services.load_playlist(repo, playlist_id))

    @app.delete("/playlists/{playlist_id}", status_code=204, tags=["playlists"])
    def delete_playlist(
        playlist_id: str,
        repo: RepositoryDep,
        force: Annotated[bool, Query(description="also delete the playlist's runs")] = False,
    ) -> Response:
        """204; 409 ``playlist_has_runs`` when runs exist and ``force`` is off."""
        services.delete_playlist(repo, playlist_id, force=force)
        return Response(status_code=204)

    @app.post(
        "/playlists/{playlist_id}/analyze",
        response_model=CoverageReport,
        tags=["features"],
    )
    def analyze(
        playlist_id: str, body: AnalyzeRequest, repo: RepositoryDep, request: Request
    ) -> CoverageReport:
        """FR-3 — resolve features from the configured providers."""
        providers = services.default_providers(body.catalog or request.app.state.catalog_path)
        analyzer = None
        if body.local:
            from flowlist.adapters.analyzer import LibrosaLocalAnalyzer

            analyzer = LibrosaLocalAnalyzer()
        return services.analyze_playlist(
            repo,
            playlist_id,
            now=_now(),
            providers=providers,
            analyzer=analyzer,
            precedence=_precedence(body.providers),
        )

    @app.post("/playlists/{playlist_id}/score", response_model=FlowReportOut, tags=["scoring"])
    def score(playlist_id: str, body: ScoreRequest, repo: RepositoryDep) -> FlowReportOut:
        """FR-7 — score the stored order."""
        weights = _weights(body.weights)
        _, report = services.score_playlist(
            repo, playlist_id, weights=weights, profile=body.profile
        )
        return FlowReportOut.of(report, weights, body.profile)

    @app.post(
        "/playlists/{playlist_id}/reorder",
        response_model=RunOut,
        status_code=201,
        tags=["scoring"],
    )
    def reorder(playlist_id: str, body: ReorderRequest, repo: RepositoryDep) -> RunOut:
        """FR-8/9/10/11 — reorder and persist an append-only run."""
        params = ReorderParams(
            seed=body.seed,
            weights=_weights(body.weights),
            profile=body.profile,
            start_entry=body.start_entry,
            end_entry=body.end_entry,
            max_passes=body.max_passes,
        )
        outcome = services.reorder_playlist(repo, playlist_id, params, now=_now(), apply=body.apply)
        return _run_out(services.load_run(repo, outcome.run.id))

    # ---------------------------------------------------------------- tracks
    @app.get("/tracks/{track_id}", response_model=TrackOut, tags=["features"])
    def get_track(track_id: str, repo: RepositoryDep) -> TrackOut:
        track = services.require_track(repo, track_id)
        rows = repo.get_features(track_id)
        resolved = resolve_features(rows, track_id=track_id)
        return TrackOut(
            track=track,
            features=rows,
            resolved=resolved.snapshot(),
            feature_sources={
                field: source.value for field, source in resolved.field_sources.items()
            },
        )

    @app.put("/tracks/{track_id}/features", response_model=TrackOut, tags=["features"])
    def put_features(track_id: str, body: ManualFeaturesRequest, repo: RepositoryDep) -> TrackOut:
        """FR-4 — the manual override, top of the resolution precedence."""
        key_pc = mode = None
        if body.key is not None:
            try:
                key_pc, mode = parse_key(body.key)
            except ValueError as exc:
                raise FlowlistError(f"unrecognised key {body.key!r}", key=body.key) from exc
        services.set_manual_features(
            repo,
            track_id,
            now=_now(),
            bpm=body.bpm,
            key_pc=key_pc,
            mode=mode,
            energy=body.energy,
            danceability=body.danceability,
            loudness_db=body.loudness_db,
            valence=body.valence,
        )
        return get_track(track_id, repo)

    # ------------------------------------------------------------------ runs
    @app.get("/runs", response_model=list[RunSummaryOut], tags=["runs"])
    def list_runs(
        repo: RepositoryDep, playlist_id: Annotated[str | None, Query()] = None
    ) -> list[RunSummaryOut]:
        applied = {
            playlist.applied_run_id for playlist in repo.list_playlists() if playlist.applied_run_id
        }
        return [
            RunSummaryOut(
                id=run.id,
                playlist_id=run.playlist_id,
                created_at=run.created_at,
                seed=run.seed,
                algorithm=run.algorithm.value,
                profile=run.params.profile,
                score_mean_before=run.score_mean_before,
                score_mean_after=run.score_mean_after,
                applied=run.id in applied,
            )
            for run in repo.list_runs(playlist_id)
        ]

    @app.get("/runs/{run_id}", response_model=RunOut, tags=["runs"])
    def get_run(run_id: str, repo: RepositoryDep) -> RunOut:
        return _run_out(services.load_run(repo, run_id))

    @app.post("/runs/{run_id}/apply", response_model=PlaylistOut, tags=["runs"])
    def apply_run(run_id: str, repo: RepositoryDep) -> PlaylistOut:
        """FR-12 — rewrite the playlist's entry positions to this ordering."""
        playlist = services.apply_run(repo, run_id)
        return _playlist_out(services.load_playlist(repo, playlist.id))

    @app.get("/runs/{run_id}/export", tags=["runs"])
    def export_run(
        run_id: str,
        repo: RepositoryDep,
        fmt: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.M3U,
    ) -> Response:
        """FR-12 — M3U8, CSV or JSON as a downloadable file response."""
        from flowlist.adapters.writers import writer_for

        view = services.load_run(repo, run_id)
        body = writer_for(fmt).render(services.build_export(view))
        media_type, suffix = _EXPORT_MEDIA[fmt]
        return PlainTextResponse(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{view.playlist.name}.{suffix}"'
            },
        )


def default_app() -> FastAPI:
    """``uvicorn`` entry point using the default database (SCOPE CLI)."""
    from flowlist.store import DEFAULT_DB_PATH

    return create_app(db_path=DEFAULT_DB_PATH)


__all__ = ["STATUS_BY_CODE", "create_app", "default_app", "get_repository"]
