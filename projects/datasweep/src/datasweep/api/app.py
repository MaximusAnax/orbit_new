"""FastAPI application (SCOPE.md §Architecture-API, FR-14).

Thin by construction: every handler parses, delegates to
:class:`~datasweep.services.DatasweepService`, and serializes.  No business
rule lives in this module — engine/service errors are translated to their
documented 4xx codes by a single exception handler.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__
from ..adapters.notifier import LogNotifier
from ..engine.models import (
    ColumnProfile,
    IssueSummary,
    ReviewItem,
    Revision,
    Run,
    RunStatus,
    RunSummary,
    WatchedFolder,
)
from ..errors import DatasweepError, UnknownRunError
from ..services import DatasweepService, ProfileResult, RevertCheck, RunDetail, ScanResult
from ..store.sqlite import SqliteRepository
from .schemas import (
    CleanRequest,
    DecisionRequest,
    FolderCreate,
    HealthResponse,
    ScanRequest,
)

#: Environment variable that points the default backend at a database file.
DB_ENV = "DATASWEEP_DB"


def default_db_path() -> Path:
    configured = os.environ.get(DB_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".datasweep" / "datasweep.db"


def default_service_factory() -> Iterator[DatasweepService]:
    """Production wiring: SQLite store, system clock, log notifier."""
    path = default_db_path()
    repository = SqliteRepository(path)
    try:
        yield DatasweepService(repository, notifier=LogNotifier(path.parent / "notify.log"))
    finally:
        repository.close()


def create_app(
    service_factory: Callable[[], DatasweepService | Iterator[DatasweepService]] | None = None,
) -> FastAPI:
    """Build the app.  Tests inject a factory bound to an in-memory store."""
    app = FastAPI(
        title="datasweep",
        version=__version__,
        summary="Background, non-destructive cleaner for tabular data files.",
    )
    provider = service_factory or default_service_factory

    def get_service() -> Iterator[DatasweepService]:
        """Accept either a plain factory or a generator factory that cleans up."""
        produced = provider()
        if isinstance(produced, DatasweepService):
            yield produced
        else:
            yield from produced

    Service = Annotated[DatasweepService, Depends(get_service)]

    @app.exception_handler(DatasweepError)
    async def _datasweep_error(request: Request, exc: DatasweepError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"detail": exc.as_dict()})

    # -- health ------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    # -- watched folders (FR-1) -------------------------------------------

    @app.get("/folders", response_model=list[WatchedFolder])
    def list_folders(service: Service) -> list[WatchedFolder]:
        return service.list_folders()

    @app.post("/folders", response_model=WatchedFolder, status_code=status.HTTP_201_CREATED)
    def create_folder(body: FolderCreate, service: Service) -> WatchedFolder:
        return service.add_folder(
            body.path,
            recursive=body.recursive,
            include=body.include,
            policy_path=body.policy_path,
            output_dir=body.output_dir,
        )

    @app.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_folder(folder_id: str, service: Service) -> Response:
        if not service.delete_folder(folder_id):
            raise UnknownRunError(f"unknown folder: {folder_id}", folder_id=folder_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # -- processing (FR-1/2/13, US-5) -------------------------------------

    @app.post("/scan", response_model=ScanResult)
    def scan(service: Service, body: ScanRequest | None = None) -> ScanResult:
        return service.scan_once(force=bool(body and body.force))

    @app.post("/clean", response_model=Run, status_code=status.HTTP_201_CREATED)
    def clean(body: CleanRequest, service: Service) -> Run:
        return service.clean_file(
            body.path,
            out=body.out,
            policy_path=body.policy_path,
            force=body.force,
            sheet=body.sheet,
        )

    @app.post("/profile", response_model=ProfileResult)
    def profile(body: CleanRequest, service: Service) -> ProfileResult:
        return service.profile_file(body.path, policy_path=body.policy_path, sheet=body.sheet)

    # -- runs (FR-12) ------------------------------------------------------

    @app.get("/runs", response_model=list[RunSummary])
    def list_runs(
        service: Service,
        path: Annotated[str | None, Query()] = None,
        run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    ) -> list[RunSummary]:
        runs = service.list_runs(path=path, status=run_status)
        return [service.run_summary(run) for run in runs]

    @app.get("/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str, service: Service) -> RunDetail:
        return service.run_detail(run_id)

    @app.get("/runs/{run_id}/audit")
    def get_audit(run_id: str, service: Service) -> FileResponse:
        run = service.get_run(run_id)
        revisions = service.repo.list_revisions(run_id)
        name = Path(revisions[-1].audit_path).name if revisions else "audit.jsonl"
        return FileResponse(
            service.artifact_path(run, name), media_type="application/x-ndjson", filename=name
        )

    @app.get("/runs/{run_id}/findings")
    def get_findings(run_id: str, service: Service) -> FileResponse:
        run = service.get_run(run_id)
        return FileResponse(
            service.artifact_path(run, "findings.jsonl"),
            media_type="application/x-ndjson",
            filename="findings.jsonl",
        )

    @app.get("/runs/{run_id}/report")
    def get_report(run_id: str, service: Service) -> FileResponse:
        run = service.get_run(run_id)
        return FileResponse(
            service.artifact_path(run, "report.md"),
            media_type="text/markdown",
            filename="report.md",
        )

    @app.get("/runs/{run_id}/columns", response_model=list[ColumnProfile])
    def get_columns(run_id: str, service: Service) -> list[ColumnProfile]:
        service.get_run(run_id)
        return service.repo.list_column_profiles(run_id)

    @app.get("/runs/{run_id}/issues", response_model=list[IssueSummary])
    def get_issues(run_id: str, service: Service) -> list[IssueSummary]:
        service.get_run(run_id)
        return service.repo.list_issue_summaries(run_id)

    # -- review workflow (FR-11) ------------------------------------------

    @app.get("/runs/{run_id}/review", response_model=list[ReviewItem])
    def get_review(run_id: str, service: Service) -> list[ReviewItem]:
        return service.review_items(run_id)

    @app.post(
        "/runs/{run_id}/decisions", response_model=Revision, status_code=status.HTTP_201_CREATED
    )
    def decisions(run_id: str, body: DecisionRequest, service: Service) -> Revision:
        return service.decide(run_id, accept=body.accept, reject=body.reject)

    # -- reversibility (FR-9) ---------------------------------------------

    @app.post("/runs/{run_id}/revert", response_model=RevertCheck)
    def revert_run(run_id: str, service: Service) -> RevertCheck:
        return service.revert_check(run_id)

    return app


app = create_app()

__all__ = ["DB_ENV", "app", "create_app", "default_db_path", "default_service_factory"]
