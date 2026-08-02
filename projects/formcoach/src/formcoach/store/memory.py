"""Pure-Python :class:`~formcoach.store.repository.Repository` for tests.

Same semantics as the SQLite backend — singleton profile, at most one active
program, append-only logs and analyses, identical query filters — with no SQL
in the way.  Stored models are deep-copied on the way in and out so a caller
cannot mutate the store by holding onto a reference.
"""

from __future__ import annotations

from collections.abc import Sequence

from formcoach.models import (
    AnalysisResult,
    Datasets,
    Equipment,
    Exercise,
    FormAnalysis,
    LoggedSession,
    MediaAsset,
    MovementPattern,
    Muscle,
    Prescription,
    Program,
    ProgramPlan,
    ProgramSession,
    ProgramStatus,
    SessionPlan,
    SetLog,
    UserProfile,
    VolumeLandmark,
    WorkoutLog,
)
from formcoach.store.repository import RepositoryError, matches_exercise_filter


class InMemoryRepository:
    """Ephemeral backend used by unit tests."""

    def __init__(self) -> None:
        self._exercises: dict[str, Exercise] = {}
        self._media: list[MediaAsset] = []
        self._landmarks: dict[Muscle, VolumeLandmark] = {}
        self._profile: UserProfile | None = None
        self._programs: dict[int, Program] = {}
        self._sessions: dict[int, ProgramSession] = {}
        self._prescriptions: dict[int, list[Prescription]] = {}
        self._workouts: list[LoggedSession] = []
        self._analyses: list[AnalysisResult] = []
        self._next_id = {
            "program": 1,
            "session": 1,
            "prescription": 1,
            "workout": 1,
            "set": 1,
            "analysis": 1,
            "rep": 1,
            "finding": 1,
        }

    def _take(self, kind: str) -> int:
        value = self._next_id[kind]
        self._next_id[kind] = value + 1
        return value

    # -- lifecycle ---------------------------------------------------------

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    # -- committed library -------------------------------------------------

    def load_library(self, datasets: Datasets) -> None:
        self._exercises = {e.id: e for e in datasets.exercises}
        self._media = list(datasets.media_assets)
        self._landmarks = dict(datasets.landmarks)

    def list_exercises(
        self,
        *,
        muscle: Muscle | None = None,
        equipment: Equipment | None = None,
        pattern: MovementPattern | None = None,
        analyzable: bool | None = None,
    ) -> list[Exercise]:
        return [
            e
            for e in sorted(self._exercises.values(), key=lambda x: x.id)
            if matches_exercise_filter(
                e, muscle=muscle, equipment=equipment, pattern=pattern, analyzable=analyzable
            )
        ]

    def get_exercise(self, exercise_id: str) -> Exercise | None:
        return self._exercises.get(exercise_id)

    def list_media(self, exercise_id: str) -> list[MediaAsset]:
        return sorted((a for a in self._media if a.exercise_id == exercise_id), key=lambda a: a.id)

    def get_landmark(self, muscle: Muscle) -> VolumeLandmark | None:
        return self._landmarks.get(muscle)

    # -- profile -----------------------------------------------------------

    def get_profile(self) -> UserProfile | None:
        return self._profile.model_copy(deep=True) if self._profile else None

    def save_profile(self, profile: UserProfile) -> UserProfile:
        self._profile = profile.model_copy(deep=True)
        return profile

    # -- programs ----------------------------------------------------------

    def save_program(self, plan: ProgramPlan) -> ProgramPlan:
        for program_id, program in self._programs.items():
            if program.status is ProgramStatus.ACTIVE:
                self._programs[program_id] = program.model_copy(
                    update={"status": ProgramStatus.ARCHIVED}
                )
        program_id = self._take("program")
        stored_program = plan.program.model_copy(update={"id": program_id})
        self._programs[program_id] = stored_program

        stored_sessions: list[SessionPlan] = []
        for session in plan.sessions:
            session_id = self._take("session")
            self._sessions[session_id] = ProgramSession(
                id=session_id,
                program_id=program_id,
                week=session.week,
                day_index=session.day_index,
                name=session.name,
            )
            stored = [
                p.model_copy(update={"id": self._take("prescription"), "session_id": session_id})
                for p in session.prescriptions
            ]
            self._prescriptions[session_id] = stored
            stored_sessions.append(session.model_copy(update={"prescriptions": stored}))
        return ProgramPlan(program=stored_program, sessions=stored_sessions)

    def get_program(self, program_id: int) -> Program | None:
        return self._programs.get(program_id)

    def list_programs(self) -> list[Program]:
        return [self._programs[k] for k in sorted(self._programs)]

    def active_program(self) -> Program | None:
        active = [p for p in self.list_programs() if p.status is ProgramStatus.ACTIVE]
        return active[-1] if active else None

    def set_program_status(self, program_id: int, status: ProgramStatus) -> None:
        if program_id not in self._programs:
            raise RepositoryError(f"no such program: {program_id}")
        if status is ProgramStatus.ACTIVE:
            for other_id, program in self._programs.items():
                if program.status is ProgramStatus.ACTIVE:
                    self._programs[other_id] = program.model_copy(
                        update={"status": ProgramStatus.ARCHIVED}
                    )
        self._programs[program_id] = self._programs[program_id].model_copy(
            update={"status": status}
        )

    def list_sessions(self, program_id: int) -> list[ProgramSession]:
        return sorted(
            (s for s in self._sessions.values() if s.program_id == program_id),
            key=lambda s: (s.week, s.day_index),
        )

    def get_session(self, program_id: int, week: int, day_index: int) -> ProgramSession | None:
        for session in self._sessions.values():
            if (
                session.program_id == program_id
                and session.week == week
                and session.day_index == day_index
            ):
                return session
        return None

    def list_prescriptions(self, session_id: int) -> list[Prescription]:
        return sorted(self._prescriptions.get(session_id, []), key=lambda p: p.position)

    # -- logs --------------------------------------------------------------

    def add_workout(self, workout: WorkoutLog, sets: Sequence[SetLog]) -> LoggedSession:
        if not sets:
            raise RepositoryError("a workout must carry at least one set")
        workout_id = self._take("workout")
        stored = LoggedSession(
            workout=workout.model_copy(update={"id": workout_id}),
            sets=[
                s.model_copy(update={"id": self._take("set"), "workout_id": workout_id})
                for s in sets
            ],
        )
        self._workouts.append(stored)
        return stored

    def list_workouts(self, since: str | None = None) -> list[LoggedSession]:
        rows = [s for s in self._workouts if since is None or s.workout.performed_at >= since]
        return sorted(rows, key=lambda s: (s.workout.performed_at, s.workout.id or 0))

    def workouts_for_session(self, session_id: int) -> list[LoggedSession]:
        return [s for s in self.list_workouts() if s.workout.program_session_id == session_id]

    # -- form analyses -----------------------------------------------------

    def save_analysis(self, result: AnalysisResult) -> AnalysisResult:
        analysis_id = self._take("analysis")
        reps = []
        for rep in result.reps:
            rep_id = self._take("rep")
            findings = [
                f.model_copy(update={"id": self._take("finding"), "rep_analysis_id": rep_id})
                for f in rep.findings
            ]
            reps.append(
                rep.model_copy(
                    update={"id": rep_id, "analysis_id": analysis_id, "findings": findings}
                )
            )
        stored = AnalysisResult(
            analysis=result.analysis.model_copy(update={"id": analysis_id}),
            reps=reps,
            corrections=result.corrections,
        )
        self._analyses.append(stored)
        return stored

    def get_analysis(self, analysis_id: int) -> AnalysisResult | None:
        for result in self._analyses:
            if result.analysis.id == analysis_id:
                return result
        return None

    def list_analyses(self, exercise_id: str | None = None) -> list[FormAnalysis]:
        return [
            r.analysis
            for r in self._analyses
            if exercise_id is None or r.analysis.exercise_id == exercise_id
        ]
