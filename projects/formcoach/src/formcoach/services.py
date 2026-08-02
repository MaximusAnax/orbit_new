"""Application services — the one place the API and the CLI both call.

The layering rule in ``CONVENTIONS.md`` says the API and CLI are thin: parse,
delegate, serialize.  This module is what they delegate *to*.  It owns no
business rules of its own either; it wires the repository, the committed
datasets and the offline adapters together and calls the engine, translating
engine-level failures into the error codes ``docs/SCOPE.md`` names.

Everything time-shaped is an argument: ``now``/``as_of``/``created_at`` are
supplied by the caller, never read from the clock here.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from formcoach.adapters.media_local import LocalMediaResolver
from formcoach.adapters.pose import PoseEstimationError
from formcoach.adapters.pose_fixture import FixturePoseEstimator, parse_sidecar
from formcoach.engine.programming import (
    ProgramGenerationError,
    generate_program,
    next_session,
)
from formcoach.engine.progression import next_prescription
from formcoach.engine.report import (
    NotAnalyzableError,
    analyze_clip,
    analyze_photo,
    profile_for,
)
from formcoach.engine.safety import (
    STOP_AND_REFER,
    SafetyGateError,
    clear_pain_flag,
    flagged_exercise_ids,
    pain_flags_after,
    require_disclaimer_ack,
)
from formcoach.engine.volume import iso_week_of, weekly_volume_report
from formcoach.models import (
    AnalysisResult,
    Datasets,
    DeclaredView,
    Equipment,
    Exercise,
    FormAnalysis,
    LoggedSession,
    MediaAsset,
    MovementPattern,
    Muscle,
    NextPrescription,
    Phase,
    Program,
    ProgramPlan,
    ProgramSession,
    SetLog,
    Unit,
    UserProfile,
    VolumeReport,
    WorkoutLog,
    lb_to_kg,
)
from formcoach.store.datasets import DatasetError, load_datasets
from formcoach.store.repository import Repository, RepositoryError
from formcoach.store.sqlite import SQLiteRepository

DB_PATH_ENV = "FORMCOACH_DB"
DEFAULT_DB_PATH = Path.home() / ".formcoach" / "formcoach.db"


class ServiceError(RuntimeError):
    """A failure with one of the error codes SCOPE.md's catalog defines."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


#: The error catalog.  ``status`` is the HTTP code the API maps the code onto;
#: the CLI turns every one of them into a non-zero exit.
ERROR_STATUS: dict[str, int] = {
    "profile_not_found": 404,
    "exercise_not_found": 404,
    "program_not_found": 404,
    "session_not_found": 404,
    "analysis_not_found": 404,
    "program_complete": 404,
    "no_active_program": 404,
    "disclaimer_not_acknowledged": 409,
    "invalid_reference": 400,
    "not_analyzable": 400,
    "pose_unavailable": 400,
    "program_generation_failed": 400,
    "dataset_error": 500,
}


def _fail(code: str, message: str) -> ServiceError:
    return ServiceError(code, message, ERROR_STATUS.get(code, 400))


def default_db_path() -> Path:
    override = os.environ.get(DB_PATH_ENV)
    return Path(override) if override else DEFAULT_DB_PATH


def open_repository(db_path: str | Path | None = None) -> SQLiteRepository:
    """Open (and create if needed) the SQLite store."""
    path = Path(db_path) if db_path is not None else default_db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    repo = SQLiteRepository(path)
    repo.initialize()
    return repo


@dataclass(frozen=True)
class LogResult:
    """What logging a workout produced, including the FR-13(b) response."""

    session: LoggedSession
    flagged_exercise_ids: list[str]
    recommendation: str | None


@dataclass(frozen=True)
class NextSessionResult:
    """FR-6 + FR-13(b) applied to the next unlogged session."""

    program: Program
    session: ProgramSession
    prescriptions: list[NextPrescription]
    dropped: list[dict[str, str]]


class FormCoachService:
    """Everything the surfaces can ask FormCoach to do."""

    def __init__(
        self,
        repository: Repository,
        datasets: Datasets | None = None,
        *,
        data_dir: str | Path | None = None,
        pose_estimator=None,
    ) -> None:
        try:
            self.datasets = datasets if datasets is not None else load_datasets(data_dir)
        except DatasetError as exc:  # pragma: no cover - surfaced at init only
            raise _fail("dataset_error", str(exc)) from exc
        self.repository = repository
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.media = LocalMediaResolver(
            self.datasets.media_assets,
            self.data_dir or Path(__file__).resolve().parent.parent.parent / "data",
        )
        self.pose = pose_estimator or FixturePoseEstimator()

    # -- lifecycle ---------------------------------------------------------

    def initialize(self) -> int:
        """FR-2 ``init``: create the schema and load the committed library."""
        self.repository.initialize()
        self.repository.load_library(self.datasets)
        return len(self.datasets.exercises)

    # -- FR-1 profile ------------------------------------------------------

    def get_profile(self) -> UserProfile:
        profile = self.repository.get_profile()
        if profile is None:
            raise _fail("profile_not_found", "No profile yet — create one with PUT /profile.")
        return profile

    def profile_or_none(self) -> UserProfile | None:
        return self.repository.get_profile()

    def save_profile(self, profile: UserProfile) -> UserProfile:
        unknown = [
            exercise_id
            for exercise_id in profile.pain_flags
            if self.repository.get_exercise(exercise_id) is None
        ]
        if unknown:
            raise _fail("invalid_reference", f"unknown exercise id(s): {', '.join(unknown)}")
        return self.repository.save_profile(profile)

    def acknowledge_disclaimer(self, now: str) -> UserProfile:
        profile = self.get_profile()
        return self.repository.save_profile(
            profile.model_copy(update={"disclaimer_acknowledged_at": now, "updated_at": now})
        )

    def clear_pain_flag(self, exercise_id: str, now: str) -> UserProfile:
        profile = self.get_profile()
        if exercise_id not in profile.pain_flags:
            raise _fail("invalid_reference", f"{exercise_id} is not flagged")
        return self.repository.save_profile(
            profile.model_copy(
                update={"pain_flags": clear_pain_flag(profile, exercise_id), "updated_at": now}
            )
        )

    # -- FR-2 library ------------------------------------------------------

    def list_exercises(
        self,
        *,
        muscle: Muscle | None = None,
        equipment: Equipment | None = None,
        pattern: MovementPattern | None = None,
        analyzable: bool | None = None,
    ) -> list[Exercise]:
        return self.repository.list_exercises(
            muscle=muscle, equipment=equipment, pattern=pattern, analyzable=analyzable
        )

    def get_exercise(self, token: str) -> tuple[Exercise, list[MediaAsset]]:
        exercise = self.repository.get_exercise(token) or self.datasets.find_exercise(token)
        if exercise is None:
            raise _fail("exercise_not_found", f"no exercise {token!r}")
        return exercise, self.media.resolve(exercise.id)

    # -- FR-3 programs -----------------------------------------------------

    def create_program(
        self, *, as_of: str, seed: int, overrides: dict | None = None
    ) -> ProgramPlan:
        profile = self.get_profile()
        if overrides:
            profile = profile.model_copy(update=overrides)
        try:
            require_disclaimer_ack(profile)
        except SafetyGateError as exc:
            raise _fail(exc.code, str(exc)) from exc
        try:
            plan = generate_program(profile, self.datasets, as_of=as_of, seed=seed)
        except ProgramGenerationError as exc:
            raise _fail("program_generation_failed", str(exc)) from exc
        return self.repository.save_program(plan)

    def get_program(self, program_id: int) -> Program:
        program = self.repository.get_program(program_id)
        if program is None:
            raise _fail("program_not_found", f"no program {program_id}")
        return program

    def active_program(self) -> Program:
        program = self.repository.active_program()
        if program is None:
            raise _fail("no_active_program", "no active program — generate one first")
        return program

    def list_programs(self) -> list[Program]:
        return self.repository.list_programs()

    def get_session(self, program_id: int, week: int, day_index: int):
        self.get_program(program_id)
        session = self.repository.get_session(program_id, week, day_index)
        if session is None:
            raise _fail(
                "session_not_found", f"program {program_id} has no week {week} day {day_index}"
            )
        assert session.id is not None
        return session, self.repository.list_prescriptions(session.id)

    def next_session(self, program_id: int) -> NextSessionResult:
        """US-4 / FR-13(b): the next unlogged session, progressed and substituted."""
        from formcoach.engine.safety import resolve_for_pain

        program = self.get_program(program_id)
        profile = self.get_profile()
        sessions = self.repository.list_sessions(program_id)
        logged = {
            s.id
            for s in sessions
            if s.id is not None and self.repository.workouts_for_session(s.id)
        }
        session = next_session(sessions, logged)
        if session is None:
            raise _fail("program_complete", f"every session of program {program_id} is logged")
        assert session.id is not None

        history = self.repository.list_workouts()
        prescriptions: list[NextPrescription] = []
        dropped: list[dict[str, str]] = []
        for prescription in self.repository.list_prescriptions(session.id):
            resolution = resolve_for_pain(
                prescription,
                profile,
                self.datasets,
                program.target_muscles,
                seed=program.seed,
            )
            if resolution.exercise is None:
                dropped.append(
                    {
                        "exercise_id": prescription.exercise_id,
                        "reason": resolution.dropped_reason or "unavailable",
                    }
                )
                continue
            exercise = resolution.exercise
            variant = (
                self.datasets.exercise(exercise.harder_variant_id)
                if exercise.harder_variant_id
                else None
            )
            result = next_prescription(
                history,
                prescription,
                exercise,
                week=session.week,
                increment_kg=self.datasets.increment_kg(exercise),
                harder_variant=variant,
            )
            if resolution.substituted_for:
                result = result.model_copy(update={"substituted_for": resolution.substituted_for})
            prescriptions.append(result)
        return NextSessionResult(
            program=program, session=session, prescriptions=prescriptions, dropped=dropped
        )

    # -- FR-5 logging ------------------------------------------------------

    def log_workout(
        self,
        *,
        performed_at: str,
        sets: Sequence[dict],
        program_session_id: int | None = None,
        notes: str | None = None,
        unit: Unit | None = None,
    ) -> LogResult:
        profile = self.profile_or_none()
        display_unit = unit or (profile.unit if profile else Unit.KG)
        if program_session_id is not None:
            known = any(
                s.id == program_session_id
                for program in self.repository.list_programs()
                if program.id is not None
                for s in self.repository.list_sessions(program.id)
            )
            if not known:
                raise _fail("invalid_reference", f"no program session {program_session_id}")

        counters: dict[str, int] = {}
        rows: list[SetLog] = []
        for entry in sets:
            exercise_id = entry["exercise_id"]
            if self.repository.get_exercise(exercise_id) is None:
                raise _fail("invalid_reference", f"unknown exercise id {exercise_id!r}")
            weight = float(entry.get("weight", 0.0) or 0.0)
            weight_kg = weight if display_unit is Unit.KG else lb_to_kg(weight)
            index = counters.get(exercise_id, 0)
            counters[exercise_id] = index + 1
            rows.append(
                SetLog(
                    exercise_id=exercise_id,
                    set_index=index,
                    weight_kg=round(weight_kg, 6),
                    reps=int(entry["reps"]),
                    rir=entry.get("rir"),
                    pain_flag=bool(entry.get("pain_flag", False)),
                )
            )
        if not rows:
            raise _fail("invalid_reference", "a workout must carry at least one set")

        try:
            session = self.repository.add_workout(
                WorkoutLog(
                    performed_at=performed_at,
                    program_session_id=program_session_id,
                    notes=notes,
                ),
                rows,
            )
        except RepositoryError as exc:
            raise _fail("invalid_reference", str(exc)) from exc

        flagged = flagged_exercise_ids(rows)
        if flagged and profile is not None:
            self.repository.save_profile(
                profile.model_copy(
                    update={
                        "pain_flags": pain_flags_after(profile, rows),
                        "updated_at": performed_at,
                    }
                )
            )
        return LogResult(
            session=session,
            flagged_exercise_ids=flagged,
            recommendation=STOP_AND_REFER if flagged else None,
        )

    def list_workouts(self, since: str | None = None) -> list[LoggedSession]:
        return self.repository.list_workouts(since)

    # -- FR-4 volume -------------------------------------------------------

    def volume_report(
        self, iso_week: str | None = None, *, as_of: str | None = None
    ) -> VolumeReport:
        if iso_week is None:
            if as_of is None:
                raise _fail("invalid_reference", "supply either iso_week or as_of")
            iso_week = iso_week_of(as_of)
        program = self.repository.active_program()
        targets = program.target_muscles if program else ()
        return weekly_volume_report(
            self.repository.list_workouts(), self.datasets, iso_week, targets
        )

    # -- FR-10 / FR-15 form analysis ---------------------------------------

    def _sequence(self, *, source: str | None, keypoints: dict | None, declared_view):
        if keypoints is not None:
            return parse_sidecar(keypoints, source_ref=source or "inline")
        if source is None:
            raise _fail("pose_unavailable", "supply either a media path or inline keypoints")
        try:
            return self.pose.estimate(source, declared_view=declared_view)
        except PoseEstimationError as exc:
            raise _fail("pose_unavailable", str(exc)) from exc

    def _analyzable(self, token: str):
        exercise = self.repository.get_exercise(token) or self.datasets.find_exercise(token)
        if exercise is None:
            raise _fail("exercise_not_found", f"no exercise {token!r}")
        try:
            return exercise, profile_for(self.datasets, exercise)
        except NotAnalyzableError as exc:
            raise _fail("not_analyzable", str(exc)) from exc

    def analyze(
        self,
        *,
        exercise_id: str,
        created_at: str,
        source: str | None = None,
        keypoints: dict | None = None,
        view: DeclaredView | None = None,
    ) -> AnalysisResult:
        exercise, profile = self._analyzable(exercise_id)
        sequence = self._sequence(source=source, keypoints=keypoints, declared_view=view)
        result = analyze_clip(
            sequence,
            exercise=exercise,
            profile=profile,
            created_at=created_at,
            declared_view=view,
        )
        return self.repository.save_analysis(result)

    def analyze_photo(
        self,
        *,
        exercise_id: str,
        created_at: str,
        phase: Phase,
        source: str | None = None,
        keypoints: dict | None = None,
        view: DeclaredView | None = None,
    ) -> AnalysisResult:
        exercise, profile = self._analyzable(exercise_id)
        sequence = self._sequence(source=source, keypoints=keypoints, declared_view=view)
        try:
            result = analyze_photo(
                sequence,
                exercise=exercise,
                profile=profile,
                created_at=created_at,
                phase=phase,
                declared_view=view,
            )
        except ValueError as exc:
            raise _fail("invalid_reference", str(exc)) from exc
        return self.repository.save_analysis(result)

    def get_analysis(self, analysis_id: int) -> AnalysisResult:
        result = self.repository.get_analysis(analysis_id)
        if result is None:
            raise _fail("analysis_not_found", f"no analysis {analysis_id}")
        return result

    def list_analyses(self, exercise_id: str | None = None) -> list[FormAnalysis]:
        return self.repository.list_analyses(exercise_id)
