"""The persistence interface every backend implements.

Two backends ship: :class:`~formcoach.store.sqlite.SQLiteRepository` (the
default, stdlib ``sqlite3``, also usable on ``":memory:"``) and
:class:`~formcoach.store.memory.InMemoryRepository` (pure Python, for tests
that want no SQL in the way).  Both satisfy this Protocol, so services and the
future API/CLI layers depend on nothing else.

Append-only entities (``workout_log``, ``set_log``, ``form_analysis`` and its
children) have no update or delete operation *by design* — DATA_MODEL makes
corrections a new workout with notes, not an edit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

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
    SetLog,
    UserProfile,
    VolumeLandmark,
    WorkoutLog,
)


class RepositoryError(RuntimeError):
    """Raised when a write would break a stored invariant."""


def matches_exercise_filter(
    exercise: Exercise,
    *,
    muscle: Muscle | None = None,
    equipment: Equipment | None = None,
    pattern: MovementPattern | None = None,
    analyzable: bool | None = None,
) -> bool:
    """FR-2 library query predicate, shared by every backend."""
    if muscle is not None and muscle not in exercise.primary_muscles:
        return False
    if equipment is not None and equipment not in exercise.equipment:
        return False
    if pattern is not None and exercise.pattern is not pattern:
        return False
    return not (analyzable is not None and exercise.is_analyzable is not analyzable)


@runtime_checkable
class Repository(Protocol):
    """Everything FormCoach persists."""

    # -- lifecycle ---------------------------------------------------------
    def initialize(self) -> None:
        """Create the schema if it is not there yet."""
        ...

    def close(self) -> None: ...

    # -- committed library -------------------------------------------------
    def load_library(self, datasets: Datasets) -> None:
        """Write the committed exercise/media/landmark datasets into the store."""
        ...

    def list_exercises(
        self,
        *,
        muscle: Muscle | None = None,
        equipment: Equipment | None = None,
        pattern: MovementPattern | None = None,
        analyzable: bool | None = None,
    ) -> list[Exercise]: ...

    def get_exercise(self, exercise_id: str) -> Exercise | None: ...

    def list_media(self, exercise_id: str) -> list[MediaAsset]: ...

    def get_landmark(self, muscle: Muscle) -> VolumeLandmark | None: ...

    # -- profile -----------------------------------------------------------
    def get_profile(self) -> UserProfile | None: ...

    def save_profile(self, profile: UserProfile) -> UserProfile: ...

    # -- programs ----------------------------------------------------------
    def save_program(self, plan: ProgramPlan) -> ProgramPlan:
        """Persist a generated plan, archiving any previously active program."""
        ...

    def get_program(self, program_id: int) -> Program | None: ...

    def list_programs(self) -> list[Program]: ...

    def active_program(self) -> Program | None: ...

    def set_program_status(self, program_id: int, status: ProgramStatus) -> None: ...

    def list_sessions(self, program_id: int) -> list[ProgramSession]: ...

    def get_session(self, program_id: int, week: int, day_index: int) -> ProgramSession | None: ...

    def list_prescriptions(self, session_id: int) -> list[Prescription]: ...

    # -- logs (append-only) ------------------------------------------------
    def add_workout(self, workout: WorkoutLog, sets: Sequence[SetLog]) -> LoggedSession: ...

    def list_workouts(self, since: str | None = None) -> list[LoggedSession]: ...

    def workouts_for_session(self, session_id: int) -> list[LoggedSession]: ...

    # -- form analyses (append-only) ---------------------------------------
    def save_analysis(self, result: AnalysisResult) -> AnalysisResult: ...

    def get_analysis(self, analysis_id: int) -> AnalysisResult | None: ...

    def list_analyses(self, exercise_id: str | None = None) -> list[FormAnalysis]: ...
