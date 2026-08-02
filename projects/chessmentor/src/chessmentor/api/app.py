"""FR-14 — the FastAPI surface.

Thin by construction: every handler parses its request, calls exactly one
:class:`~chessmentor.services.ChessMentorService` method and serializes the
result.  No chess, rating or coaching rule lives in this module; the error
catalog below is the only logic here, and it is a pure mapping from the service
error classes to status codes.

| Service error            | Status | Code                |
|--------------------------|--------|---------------------|
| ``NotInitializedError``  | 409    | ``not_initialized`` |
| ``GameInProgressError``  | 409    | ``game_in_progress`` (+ ``in_progress_game_id``) |
| ``GameFinishedError``    | 409    | ``game_finished``   |
| ``NotPlayersTurnError``  | 409    | ``not_players_turn``|
| ``AbortTooLateError``    | 409    | ``abort_too_late``  |
| ``IllegalMoveError``     | 400    | ``illegal_move`` (+ ``legal_moves_san``) |
| ``AmbiguousSideError``   | 422    | ``ambiguous_side``  |
| ``PgnParseError``        | 422    | ``pgn_parse_error`` |
| ``NotFoundError``        | 404    | ``not_found``       |
| ``AnalystUnavailableError`` | 503 | ``analyst_unavailable`` |
| other ``ServiceError``   | 400    | ``service_error``   |
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import __version__
from ..adapters.analyst import AnalystUnavailableError
from ..constants import DEEP_BUDGET, JUDGE_BUDGET
from ..engine.adapt import expected_score, target_score
from ..engine.pgn import AmbiguousSideError, PgnParseError
from ..engine.rating import blended_rating
from ..engine.session import IllegalMoveError
from ..models import AnalystKind, ChallengeMode, GameSource, GameStatus, PreferredColor
from ..services import (
    AbortTooLateError,
    ChessMentorService,
    GameFinishedError,
    GameInProgressError,
    NotInitializedError,
    NotPlayersTurnError,
    ServiceError,
)
from ..store.repository import NotFoundError
from ..store.sqlite_repo import SQLiteRepository
from .schemas import (
    AnalysisRequest,
    CreateGameRequest,
    ErrorBody,
    GameDetail,
    GameListResponse,
    HealthResponse,
    ImportedGameOut,
    ImportRequest,
    ImportResponse,
    LadderResponse,
    LevelOut,
    MoveRequest,
    MoveResponse,
    PgnResponse,
    ProfileUpdate,
    RatingHistoryResponse,
    RatingResponse,
    ReportListResponse,
    ReportRequest,
    ReportResponse,
)

__all__ = ["DB_PATH_ENV", "app", "create_app", "default_db_path", "get_service"]

DB_PATH_ENV = "CHESSMENTOR_DB"

#: FR-7d's user-facing text, surfaced by ``GET /rating`` and ``chessmentor rating``.
DIVERGENCE_WARNING_TEXT = (
    "move-quality and result estimates disagree — the ACPL anchors may not describe your play"
)


def default_db_path() -> Path:
    """``~/.chessmentor/chessmentor.db`` unless ``CHESSMENTOR_DB`` overrides it."""
    override = os.environ.get(DB_PATH_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".chessmentor" / "chessmentor.db"


def now_iso() -> str:
    """Edges may read the clock; the engine may not (CONVENTIONS.md)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_service() -> Iterator[ChessMentorService]:
    """Default dependency: a SQLite-backed service.  Tests override this.

    ``Repository.initialize`` is idempotent and only creates the schema and
    materialises the committed ladder — it never creates a profile, so an
    un-``init``-ed installation still answers ``409 not_initialized`` rather
    than silently inventing one.
    """
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    repo = SQLiteRepository(path)
    service = ChessMentorService(repo)
    repo.initialize(service.datasets.levels)
    try:
        yield service
    finally:
        repo.close()


def _error(status: int, code: str, detail: str, **extra: object) -> JSONResponse:
    body = ErrorBody(detail=detail, code=code, **extra)  # type: ignore[arg-type]
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True))


def create_app(service: ChessMentorService | None = None) -> FastAPI:
    """Build the app.  Passing ``service`` pins one instance (used by tests)."""
    api = FastAPI(
        title="ChessMentor",
        version=__version__,
        summary=(
            "A chess trainer that calibrates its opponent to you and coaches "
            "your mistakes. Ratings are an internal scale, never comparable to "
            "FIDE/Lichess."
        ),
    )

    if service is not None:

        def _pinned() -> Iterator[ChessMentorService]:
            yield service

        api.dependency_overrides[get_service] = _pinned

    # -- error catalog ------------------------------------------------------ #

    @api.exception_handler(GameInProgressError)
    async def _in_progress(_: Request, exc: GameInProgressError) -> JSONResponse:
        return _error(409, exc.code, str(exc), in_progress_game_id=exc.in_progress_game_id)

    @api.exception_handler(NotInitializedError)
    async def _not_initialized(_: Request, exc: NotInitializedError) -> JSONResponse:
        return _error(409, exc.code, str(exc))

    @api.exception_handler(GameFinishedError)
    async def _finished(_: Request, exc: GameFinishedError) -> JSONResponse:
        return _error(409, exc.code, str(exc))

    @api.exception_handler(NotPlayersTurnError)
    async def _turn(_: Request, exc: NotPlayersTurnError) -> JSONResponse:
        return _error(409, exc.code, str(exc))

    @api.exception_handler(AbortTooLateError)
    async def _abort(_: Request, exc: AbortTooLateError) -> JSONResponse:
        return _error(409, exc.code, str(exc))

    @api.exception_handler(IllegalMoveError)
    async def _illegal(_: Request, exc: IllegalMoveError) -> JSONResponse:
        return _error(400, "illegal_move", str(exc), legal_moves_san=exc.legal_san)

    @api.exception_handler(AmbiguousSideError)
    async def _ambiguous(_: Request, exc: AmbiguousSideError) -> JSONResponse:
        return _error(422, "ambiguous_side", str(exc), matches=exc.matches)

    @api.exception_handler(PgnParseError)
    async def _pgn(_: Request, exc: PgnParseError) -> JSONResponse:
        return _error(422, "pgn_parse_error", str(exc))

    @api.exception_handler(NotFoundError)
    async def _missing(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, "not_found", str(exc).strip("'"))

    @api.exception_handler(AnalystUnavailableError)
    async def _analyst(_: Request, exc: AnalystUnavailableError) -> JSONResponse:
        return _error(503, "analyst_unavailable", str(exc))

    @api.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError) -> JSONResponse:
        return _error(400, exc.code, str(exc))

    # -- health / profile / levels ------------------------------------------ #

    @api.get("/health", response_model=HealthResponse, tags=["meta"])
    def health(service: ChessMentorService = Depends(get_service)) -> HealthResponse:
        data = service.datasets
        return HealthResponse(
            engine_version=service.engine_version,
            levels=len(data.levels),
            openings=len(data.book.lines),
            advice_entries=len(data.advice),
            initialized=service.repo.get_profile() is not None,
        )

    @api.get("/profile", tags=["profile"])
    def get_profile(service: ChessMentorService = Depends(get_service)) -> dict[str, object]:
        return {"profile": service.require_profile().model_dump()}

    @api.put("/profile", tags=["profile"])
    def put_profile(
        payload: ProfileUpdate = Body(default_factory=ProfileUpdate),
        service: ChessMentorService = Depends(get_service),
    ) -> dict[str, object]:
        at = payload.at or now_iso()
        if service.repo.get_profile() is None:
            # PUT /profile is the API's `init`: it creates the profile and the
            # FR-8 cold-start rating state in one call.
            profile, _ = service.initialize(
                now=at,
                display_name=payload.display_name or "Player",
                challenge_mode=payload.challenge_mode or ChallengeMode.BALANCED,
                preferred_color=payload.preferred_color or PreferredColor.RANDOM,
            )
            return {"profile": profile.model_dump()}
        profile, _ = service.set_profile(
            now=at,
            display_name=payload.display_name,
            challenge_mode=payload.challenge_mode,
            preferred_color=payload.preferred_color,
        )
        return {"profile": profile.model_dump()}

    @api.get("/levels", response_model=LadderResponse, tags=["levels"])
    def levels(service: ChessMentorService = Depends(get_service)) -> LadderResponse:
        profile = service.require_profile()
        state = service.require_rating_state()
        r_hat = blended_rating(state)
        rungs = [
            LevelOut(
                **level.model_dump(),
                expected_score=expected_score(r_hat, level.elo_internal),
                recommended=level.id == state.current_level_id,
            )
            for level in service.datasets.levels
        ]
        return LadderResponse(
            levels=rungs,
            recommended_level_id=state.current_level_id,
            r_hat=r_hat,
            challenge_mode=profile.challenge_mode,
            target_score=target_score(profile.challenge_mode),
        )

    # -- games --------------------------------------------------------------- #

    def _detail(service: ChessMentorService, game_id: int) -> GameDetail:
        view = service.get_game(game_id)
        return GameDetail(
            game=view.game,
            fen=view.fen,
            turn=view.turn,
            player_to_move=view.player_to_move,
            legal_moves_san=list(view.legal_moves_san),
            moves=list(view.moves),
            pgn=service.game_pgn(game_id),
        )

    @api.post("/games", response_model=GameDetail, status_code=201, tags=["games"])
    def create_game(
        payload: CreateGameRequest = Body(default_factory=CreateGameRequest),
        service: ChessMentorService = Depends(get_service),
    ) -> GameDetail:
        view = service.create_game(
            started_at=payload.started_at or now_iso(),
            seed=payload.seed,
            color=payload.color,
            level_id=payload.level_id,
        )
        assert view.game.id is not None
        return _detail(service, view.game.id)

    @api.get("/games", response_model=GameListResponse, tags=["games"])
    def list_games(
        status: GameStatus | None = Query(default=None),
        source: GameSource | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=500),
        service: ChessMentorService = Depends(get_service),
    ) -> GameListResponse:
        return GameListResponse(games=service.list_games(status=status, source=source, limit=limit))

    @api.get("/games/{game_id}", response_model=GameDetail, tags=["games"])
    def get_game(game_id: int, service: ChessMentorService = Depends(get_service)) -> GameDetail:
        return _detail(service, game_id)

    @api.post("/games/{game_id}/moves", response_model=MoveResponse, tags=["games"])
    def post_move(
        game_id: int,
        payload: MoveRequest,
        service: ChessMentorService = Depends(get_service),
    ) -> MoveResponse:
        result = service.submit_move(game_id, payload.move, at=payload.at or now_iso())
        return MoveResponse(
            game=result.game,
            player_move=result.player_move,
            cpu_move=result.cpu_move,
            fen=result.fen,
            legal_moves_san=list(result.legal_moves_san),
            status=result.game.status.value,
            termination=result.game.termination.value if result.game.termination else None,
            result_score=result.game.result_score,
            analysis=result.analysis,
            rating_event=result.rating_event,
        )

    @api.post("/games/{game_id}/resign", response_model=MoveResponse, tags=["games"])
    def resign(
        game_id: int,
        payload: MoveRequest | None = Body(default=None),
        service: ChessMentorService = Depends(get_service),
    ) -> MoveResponse:
        at = payload.at if payload and payload.at else now_iso()
        result = service.resign(game_id, at=at)
        return MoveResponse(
            game=result.game,
            player_move=result.player_move,
            cpu_move=None,
            fen=result.fen,
            legal_moves_san=[],
            status=result.game.status.value,
            termination=result.game.termination.value if result.game.termination else None,
            result_score=result.game.result_score,
            analysis=result.analysis,
            rating_event=result.rating_event,
        )

    @api.post("/games/{game_id}/abort", tags=["games"])
    def abort(
        game_id: int,
        payload: MoveRequest | None = Body(default=None),
        service: ChessMentorService = Depends(get_service),
    ) -> dict[str, object]:
        at = payload.at if payload and payload.at else now_iso()
        return {"game": service.abort(game_id, at=at).model_dump()}

    @api.get("/games/{game_id}/pgn", response_model=PgnResponse, tags=["games"])
    def game_pgn(game_id: int, service: ChessMentorService = Depends(get_service)) -> PgnResponse:
        return PgnResponse(game_id=game_id, pgn=service.game_pgn(game_id))

    # -- analysis ------------------------------------------------------------ #

    @api.post("/games/{game_id}/analysis", status_code=201, tags=["analysis"])
    def create_analysis(
        game_id: int,
        payload: AnalysisRequest = Body(default_factory=AnalysisRequest),
        service: ChessMentorService = Depends(get_service),
    ) -> dict[str, object]:
        budget = payload.node_budget or (
            JUDGE_BUDGET if payload.analyst is AnalystKind.INTERNAL else DEEP_BUDGET
        )
        result = service.analyze(
            game_id,
            created_at=payload.at or now_iso(),
            analyst_kind=payload.analyst,
            node_budget=budget,
        )
        return {"analysis": result.analysis.model_dump(), "created": result.created}

    @api.get("/games/{game_id}/analysis", tags=["analysis"])
    def read_analysis(
        game_id: int,
        analysis_id: int | None = Query(default=None),
        service: ChessMentorService = Depends(get_service),
    ) -> dict[str, object]:
        return {"analysis": service.get_analysis(game_id, analysis_id=analysis_id).model_dump()}

    # -- import -------------------------------------------------------------- #

    @api.post("/imports/pgn", response_model=ImportResponse, status_code=201, tags=["import"])
    def import_pgn(
        payload: ImportRequest,
        service: ChessMentorService = Depends(get_service),
    ) -> ImportResponse:
        imported = service.import_pgn(
            payload.pgn,
            created_at=payload.at or now_iso(),
            side=payload.side,
            analyze=payload.analyze,
        )
        return ImportResponse(
            imported=[ImportedGameOut(game=item.game, analysis=item.analysis) for item in imported]
        )

    # -- rating -------------------------------------------------------------- #

    @api.get("/rating", response_model=RatingResponse, tags=["rating"])
    def rating(service: ChessMentorService = Depends(get_service)) -> RatingResponse:
        view = service.rating()
        return RatingResponse(
            state=view.state,
            r_hat=view.r_hat,
            lambda_used=view.lambda_used,
            expected_score_at_current_level=view.expected_score_at_current_level,
            recommended_level_id=view.recommended_level_id,
            calibration_warning=view.calibration_warning,
            warning_text=DIVERGENCE_WARNING_TEXT if view.calibration_warning else None,
        )

    @api.get("/rating/history", response_model=RatingHistoryResponse, tags=["rating"])
    def rating_history(
        limit: int | None = Query(default=None, ge=1, le=500),
        service: ChessMentorService = Depends(get_service),
    ) -> RatingHistoryResponse:
        return RatingHistoryResponse(events=service.rating_history(limit=limit))

    # -- coaching ------------------------------------------------------------ #

    @api.post("/coach/reports", response_model=ReportResponse, status_code=201, tags=["coach"])
    def create_report(
        payload: ReportRequest = Body(default_factory=ReportRequest),
        service: ChessMentorService = Depends(get_service),
    ) -> ReportResponse:
        view = service.create_report(
            created_at=payload.at or now_iso(),
            last_games=payload.last_games,
            include_imported=payload.include_imported,
        )
        return ReportResponse(report=view.report, deltas=view.deltas)

    @api.get("/coach/reports", response_model=ReportListResponse, tags=["coach"])
    def list_reports(
        limit: int | None = Query(default=None, ge=1, le=200),
        service: ChessMentorService = Depends(get_service),
    ) -> ReportListResponse:
        return ReportListResponse(reports=service.list_reports(limit=limit))

    @api.get("/coach/reports/{report_id}", response_model=ReportResponse, tags=["coach"])
    def get_report(
        report_id: int, service: ChessMentorService = Depends(get_service)
    ) -> ReportResponse:
        view = service.get_report(report_id)
        return ReportResponse(report=view.report, deltas=view.deltas)

    @api.get("/openapi.txt", include_in_schema=False)
    def routes() -> PlainTextResponse:
        lines = [
            f"{sorted(route.methods)[0]:6} {route.path}"
            for route in api.routes
            if getattr(route, "methods", None)
        ]
        return PlainTextResponse("\n".join(sorted(lines)))

    return api


app = create_app()
