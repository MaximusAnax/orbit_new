"""FR-11 — the FormCoach FastAPI app.

Thin by construction: each route parses its request, calls one
:class:`~formcoach.services.FormCoachService` method and serializes the result.
No business rule lives here — every rule is in ``engine/`` and every error code
comes from the service's catalog, mapped onto HTTP status codes by a single
exception handler.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.responses import JSONResponse

from formcoach.api import schemas as S
from formcoach.models import (
    Equipment,
    MovementPattern,
    Muscle,
    UserProfile,
)
from formcoach.services import FormCoachService, ServiceError, open_repository

_service: FormCoachService | None = None


def create_app(service: FormCoachService | None = None) -> FastAPI:
    """Build the app.  Tests inject a service backed by ``SQLiteRepository(":memory:")``."""
    app = FastAPI(
        title="FormCoach",
        version="0.1.0",
        summary="Science-based training programs plus pose-keypoint form analysis.",
        description=(
            "Single-user workout coach. Not a medical device: program generation is "
            "gated on an explicit not-medical-advice acknowledgement, and form reports "
            "speak only of movement faults."
        ),
    )
    app.state.service = service

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    _register(app)
    return app


def get_service(request: Request) -> FormCoachService:
    service = request.app.state.service
    if service is None:  # pragma: no cover - exercised only by the uvicorn entry point
        global _service
        if _service is None:
            _service = FormCoachService(open_repository())
            _service.initialize()
        service = _service
        request.app.state.service = service
    return service


def _exercise_out(exercise) -> dict:
    payload = exercise.model_dump()
    payload["mechanics"] = exercise.mechanics.value
    payload["analyzable"] = exercise.is_analyzable
    return payload


def _register(app: FastAPI) -> None:
    Service = Depends(get_service)

    # -- health ------------------------------------------------------------
    @app.get("/health", response_model=S.HealthOut, tags=["meta"])
    def health(service: FormCoachService = Service) -> S.HealthOut:
        return S.HealthOut(
            status="ok",
            exercises=len(service.datasets.exercises),
            form_profiles=len(service.datasets.form_profiles),
            has_profile=service.profile_or_none() is not None,
        )

    # -- FR-1 profile ------------------------------------------------------
    @app.get(
        "/profile",
        response_model=S.ProfileOut,
        tags=["profile"],
        responses={404: {"model": S.ErrorResponse}},
    )
    def read_profile(service: FormCoachService = Service) -> S.ProfileOut:
        return S.ProfileOut(**service.get_profile().model_dump())

    @app.put("/profile", response_model=S.ProfileOut, tags=["profile"])
    def put_profile(payload: S.ProfileIn, service: FormCoachService = Service) -> S.ProfileOut:
        existing = service.profile_or_none()
        profile = UserProfile(
            **payload.model_dump(),
            disclaimer_acknowledged_at=(existing.disclaimer_acknowledged_at if existing else None),
            pain_flags=list(existing.pain_flags) if existing else [],
        )
        return S.ProfileOut(**service.save_profile(profile).model_dump())

    @app.post(
        "/profile/acknowledge-disclaimer",
        response_model=S.ProfileOut,
        tags=["profile"],
        responses={404: {"model": S.ErrorResponse}},
    )
    def acknowledge(payload: S.AcknowledgeIn, service: FormCoachService = Service) -> S.ProfileOut:
        return S.ProfileOut(**service.acknowledge_disclaimer(payload.acknowledged_at).model_dump())

    @app.delete(
        "/profile/pain-flags/{exercise_id}",
        response_model=S.ProfileOut,
        tags=["profile"],
        responses={400: {"model": S.ErrorResponse}, 404: {"model": S.ErrorResponse}},
    )
    def clear_pain(
        exercise_id: str,
        cleared_at: str = Query(..., description="ISO-8601 timestamp supplied by the caller"),
        service: FormCoachService = Service,
    ) -> S.ProfileOut:
        return S.ProfileOut(**service.clear_pain_flag(exercise_id, cleared_at).model_dump())

    # -- FR-2 library ------------------------------------------------------
    @app.get("/exercises", response_model=list[S.ExerciseOut], tags=["library"])
    def list_exercises(
        muscle: Muscle | None = None,
        equipment: Equipment | None = None,
        pattern: MovementPattern | None = None,
        analyzable: bool | None = None,
        service: FormCoachService = Service,
    ) -> list[S.ExerciseOut]:
        return [
            S.ExerciseOut(**_exercise_out(e))
            for e in service.list_exercises(
                muscle=muscle, equipment=equipment, pattern=pattern, analyzable=analyzable
            )
        ]

    @app.get(
        "/exercises/{exercise_id}",
        response_model=S.ExerciseDetailOut,
        tags=["library"],
        responses={404: {"model": S.ErrorResponse}},
    )
    def read_exercise(exercise_id: str, service: FormCoachService = Service) -> S.ExerciseDetailOut:
        exercise, media = service.get_exercise(exercise_id)
        return S.ExerciseDetailOut(
            **_exercise_out(exercise),
            media=[S.MediaAssetOut(**a.model_dump()) for a in media],
        )

    # -- FR-3 programs -----------------------------------------------------
    @app.post(
        "/programs",
        response_model=S.ProgramDetailOut,
        status_code=status.HTTP_201_CREATED,
        tags=["programs"],
        responses={
            400: {"model": S.ErrorResponse},
            404: {"model": S.ErrorResponse},
            409: {"model": S.ErrorResponse, "description": "disclaimer_not_acknowledged"},
        },
    )
    def create_program(
        payload: S.ProgramIn, service: FormCoachService = Service
    ) -> S.ProgramDetailOut:
        plan = service.create_program(
            as_of=payload.as_of, seed=payload.seed, overrides=payload.overrides()
        )
        return S.ProgramDetailOut(
            **plan.program.model_dump(),
            sessions=[
                S.SessionOut(
                    id=None,
                    week=s.week,
                    day_index=s.day_index,
                    name=s.name,
                    prescriptions=[S.PrescriptionOut(**p.model_dump()) for p in s.prescriptions],
                )
                for s in plan.sessions
            ],
        )

    @app.get(
        "/programs/{program_id}",
        response_model=S.ProgramOut,
        tags=["programs"],
        responses={404: {"model": S.ErrorResponse}},
    )
    def read_program(program_id: int, service: FormCoachService = Service) -> S.ProgramOut:
        return S.ProgramOut(**service.get_program(program_id).model_dump())

    @app.get(
        "/programs/{program_id}/sessions/{week}/{day_index}",
        response_model=S.SessionOut,
        tags=["programs"],
        responses={404: {"model": S.ErrorResponse}},
    )
    def read_session(
        program_id: int, week: int, day_index: int, service: FormCoachService = Service
    ) -> S.SessionOut:
        session, prescriptions = service.get_session(program_id, week, day_index)
        return S.SessionOut(
            id=session.id,
            week=session.week,
            day_index=session.day_index,
            name=session.name,
            prescriptions=[S.PrescriptionOut(**p.model_dump()) for p in prescriptions],
        )

    @app.get(
        "/programs/{program_id}/next",
        response_model=S.NextSessionOut,
        tags=["programs"],
        responses={404: {"model": S.ErrorResponse, "description": "program_complete"}},
    )
    def read_next(program_id: int, service: FormCoachService = Service) -> S.NextSessionOut:
        result = service.next_session(program_id)
        return S.NextSessionOut(
            program_id=result.program.id,
            week=result.session.week,
            day_index=result.session.day_index,
            name=result.session.name,
            prescriptions=[S.NextPrescriptionOut(**p.model_dump()) for p in result.prescriptions],
            dropped=[S.DroppedOut(**d) for d in result.dropped],
        )

    # -- FR-5 logging ------------------------------------------------------
    @app.post(
        "/workouts",
        response_model=S.WorkoutCreatedOut,
        status_code=status.HTTP_201_CREATED,
        tags=["logs"],
        responses={400: {"model": S.ErrorResponse}},
    )
    def create_workout(
        payload: S.WorkoutIn, service: FormCoachService = Service
    ) -> S.WorkoutCreatedOut:
        result = service.log_workout(
            performed_at=payload.performed_at,
            sets=[s.model_dump() for s in payload.sets],
            program_session_id=payload.program_session_id,
            notes=payload.notes,
            unit=payload.unit,
        )
        return S.WorkoutCreatedOut(
            workout=_workout_out(result.session),
            pain_flagged=result.flagged_exercise_ids,
            recommendation=result.recommendation,
        )

    @app.get("/workouts", response_model=list[S.WorkoutOut], tags=["logs"])
    def list_workouts(
        since: str | None = None, service: FormCoachService = Service
    ) -> list[S.WorkoutOut]:
        return [_workout_out(s) for s in service.list_workouts(since)]

    # -- FR-4 volume -------------------------------------------------------
    @app.get(
        "/analytics/volume",
        response_model=S.VolumeOut,
        tags=["analytics"],
        responses={400: {"model": S.ErrorResponse}},
    )
    def volume(
        iso_week: str | None = None,
        as_of: str | None = None,
        service: FormCoachService = Service,
    ) -> S.VolumeOut:
        report = service.volume_report(iso_week, as_of=as_of)
        return S.VolumeOut(
            iso_week=report.iso_week,
            rows=[S.MuscleVolumeOut(**r.model_dump()) for r in report.rows],
        )

    # -- FR-10 / FR-15 form ------------------------------------------------
    @app.post(
        "/form/analyses",
        response_model=S.AnalysisDetailOut,
        status_code=status.HTTP_201_CREATED,
        tags=["form"],
        responses={400: {"model": S.ErrorResponse}, 404: {"model": S.ErrorResponse}},
    )
    def create_analysis(
        payload: S.AnalysisIn, service: FormCoachService = Service
    ) -> S.AnalysisDetailOut:
        return _analysis_detail(
            service.analyze(
                exercise_id=payload.exercise_id,
                created_at=payload.created_at,
                source=payload.source,
                keypoints=payload.keypoints,
                view=payload.view,
            )
        )

    @app.post(
        "/form/photo-analyses",
        response_model=S.AnalysisDetailOut,
        status_code=status.HTTP_201_CREATED,
        tags=["form"],
        responses={400: {"model": S.ErrorResponse}, 404: {"model": S.ErrorResponse}},
    )
    def create_photo_analysis(
        payload: S.PhotoAnalysisIn, service: FormCoachService = Service
    ) -> S.AnalysisDetailOut:
        return _analysis_detail(
            service.analyze_photo(
                exercise_id=payload.exercise_id,
                created_at=payload.created_at,
                phase=payload.phase,
                source=payload.source,
                keypoints=payload.keypoints,
                view=payload.view,
            )
        )

    @app.get("/form/analyses", response_model=list[S.AnalysisOut], tags=["form"])
    def list_analyses(
        exercise_id: str | None = None, service: FormCoachService = Service
    ) -> list[S.AnalysisOut]:
        return [S.AnalysisOut(**_analysis_payload(a)) for a in service.list_analyses(exercise_id)]

    @app.get(
        "/form/analyses/{analysis_id}",
        response_model=S.AnalysisDetailOut,
        tags=["form"],
        responses={404: {"model": S.ErrorResponse}},
    )
    def read_analysis(analysis_id: int, service: FormCoachService = Service) -> S.AnalysisDetailOut:
        return _analysis_detail(service.get_analysis(analysis_id))


def _workout_out(session) -> S.WorkoutOut:
    return S.WorkoutOut(
        id=session.workout.id,
        performed_at=session.workout.performed_at,
        program_session_id=session.workout.program_session_id,
        notes=session.workout.notes,
        sets=[
            S.SetOut(
                exercise_id=s.exercise_id,
                set_index=s.set_index,
                weight_kg=s.weight_kg,
                reps=s.reps,
                rir=s.rir,
                pain_flag=s.pain_flag,
                e1rm_kg=s.e1rm_kg,
            )
            for s in session.sets
        ],
    )


def _analysis_payload(analysis) -> dict:
    payload = analysis.model_dump()
    payload["pose_source"] = analysis.pose_source.value
    return payload


def _analysis_detail(result) -> S.AnalysisDetailOut:
    return S.AnalysisDetailOut(
        **_analysis_payload(result.analysis),
        reps=[
            S.RepOut(
                rep_index=rep.rep_index,
                start_frame=rep.start_frame,
                extremum_frame=rep.extremum_frame,
                end_frame=rep.end_frame,
                metrics=rep.metrics,
                score=rep.score,
                findings=[S.FindingOut(**f.model_dump()) for f in rep.findings],
            )
            for rep in result.reps
        ],
        corrections=[S.CorrectionOut(**c.model_dump()) for c in result.corrections],
    )


#: ``uvicorn formcoach.api.app:app`` serves this instance; it opens the on-disk
#: SQLite store lazily on the first request so importing the module is cheap.
app = create_app()

__all__ = ["app", "create_app", "get_service"]
