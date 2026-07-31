"""Request and response schemas for the FormCoach API (FR-11).

Pydantic v2 throughout.  Response models are thin projections of the domain
models in :mod:`formcoach.models`; they exist so the wire format is explicit and
so weights can be echoed in the caller's display unit without the domain models
learning about presentation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from formcoach.models import (
    AnalysisKind,
    AnalysisStatus,
    DeclaredView,
    Equipment,
    Experience,
    FindingStatus,
    Goal,
    MediaKind,
    MediaSource,
    MovementPattern,
    Muscle,
    NotAssessedReason,
    Phase,
    ProgramStatus,
    ProgressionAction,
    Severity,
    SplitName,
    Unit,
    View,
    VolumeBand,
)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# ------------------------------------------------------------------------ profile


class ProfileIn(BaseModel):
    """FR-1 — the whole profile is replaced on PUT (single-row entity)."""

    model_config = ConfigDict(extra="forbid")

    goal: Goal
    experience: Experience
    days_per_week: int = Field(ge=2, le=6)
    equipment: list[Equipment] = Field(min_length=1)
    emphasized_muscles: list[Muscle] = Field(default_factory=list, max_length=3)
    unit: Unit = Unit.KG
    updated_at: str


class ProfileOut(BaseModel):
    goal: Goal
    experience: Experience
    days_per_week: int
    equipment: list[Equipment]
    emphasized_muscles: list[Muscle]
    unit: Unit
    disclaimer_acknowledged_at: str | None
    pain_flags: list[str]
    updated_at: str


class AcknowledgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged_at: str


# ----------------------------------------------------------------------- library


class MediaAssetOut(BaseModel):
    id: str
    kind: MediaKind
    source: MediaSource
    ref: str
    license: str
    attribution: str | None


class ExerciseOut(BaseModel):
    id: str
    name: str
    aliases: list[str]
    primary_muscles: list[Muscle]
    secondary_muscles: list[Muscle]
    equipment: list[Equipment]
    pattern: MovementPattern
    mechanics: str
    difficulty: int
    rest_s: int
    harder_variant_id: str | None
    instructions: list[str]
    cues: list[str]
    form_profile_id: str | None
    analyzable: bool


class ExerciseDetailOut(ExerciseOut):
    media: list[MediaAssetOut]


# ---------------------------------------------------------------------- programs


class ProgramIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str
    seed: int = 20260731
    goal: Goal | None = None
    experience: Experience | None = None
    days_per_week: int | None = Field(default=None, ge=2, le=6)
    equipment: list[Equipment] | None = None
    emphasized_muscles: list[Muscle] | None = Field(default=None, max_length=3)

    def overrides(self) -> dict:
        return {
            key: value
            for key, value in self.model_dump(exclude={"as_of", "seed"}, exclude_none=True).items()
        }


class PrescriptionOut(BaseModel):
    position: int
    exercise_id: str
    sets: int
    rep_low: int
    rep_high: int
    target_rir: float
    rest_s: int
    load_note: str | None


class SessionOut(BaseModel):
    id: int | None
    week: int
    day_index: int
    name: str
    prescriptions: list[PrescriptionOut]


class ProgramOut(BaseModel):
    id: int | None
    created_at: str
    seed: int
    goal: Goal
    experience: Experience
    days_per_week: int
    emphasized_muscles: list[Muscle]
    equipment: list[Equipment]
    target_muscles: list[Muscle]
    weeks: int
    split: SplitName
    status: ProgramStatus
    weekly_set_targets: dict[Muscle, list[int]]


class ProgramDetailOut(ProgramOut):
    sessions: list[SessionOut]


class NextPrescriptionOut(PrescriptionOut):
    action: ProgressionAction
    clause: str
    suggested_load_kg: float | None
    target_reps: int
    substituted_for: str | None
    dropped_reason: str | None
    rationale: str


class DroppedOut(BaseModel):
    exercise_id: str
    reason: str


class NextSessionOut(BaseModel):
    program_id: int | None
    week: int
    day_index: int
    name: str
    prescriptions: list[NextPrescriptionOut]
    dropped: list[DroppedOut]


# --------------------------------------------------------------------------- logs


class SetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exercise_id: str
    weight: float = Field(default=0.0, ge=0.0)
    reps: int = Field(ge=1)
    rir: float | None = Field(default=None, ge=0.0, le=10.0)
    pain_flag: bool = False


class WorkoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed_at: str
    program_session_id: int | None = None
    notes: str | None = None
    unit: Unit | None = None
    sets: list[SetIn] = Field(min_length=1)


class SetOut(BaseModel):
    exercise_id: str
    set_index: int
    weight_kg: float
    reps: int
    rir: float | None
    pain_flag: bool
    e1rm_kg: float | None


class WorkoutOut(BaseModel):
    id: int | None
    performed_at: str
    program_session_id: int | None
    notes: str | None
    sets: list[SetOut]


class WorkoutCreatedOut(BaseModel):
    workout: WorkoutOut
    pain_flagged: list[str]
    recommendation: str | None


# ------------------------------------------------------------------------ volume


class MuscleVolumeOut(BaseModel):
    muscle: Muscle
    effective_sets: float
    direct_sets: int
    band: VolumeBand
    is_target: bool
    mv: int
    mev: int
    mav: int
    mrv: int


class VolumeOut(BaseModel):
    iso_week: str
    rows: list[MuscleVolumeOut]


# ------------------------------------------------------------------ form analysis


class AnalysisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exercise_id: str
    created_at: str
    source: str | None = None
    keypoints: dict | None = None
    view: DeclaredView | None = None


class PhotoAnalysisIn(AnalysisIn):
    phase: Phase


class FindingOut(BaseModel):
    fault_id: str
    status: FindingStatus
    not_assessed_reason: NotAssessedReason | None
    measured: float | None
    threshold: float
    severity: Severity
    frame: int | None
    cue: str | None


class RepOut(BaseModel):
    rep_index: int
    start_frame: int
    extremum_frame: int
    end_frame: int
    metrics: dict[str, float]
    score: float
    findings: list[FindingOut]


class CorrectionOut(BaseModel):
    fault_id: str
    severity: Severity
    occurrences: int
    cue: str


class AnalysisOut(BaseModel):
    id: int | None
    created_at: str
    analysis_kind: AnalysisKind
    exercise_id: str
    form_profile_id: str
    source_ref: str
    pose_source: str
    view: View
    view_inferred: bool
    declared_phase: Phase | None
    fps: float | None
    frames_total: int
    frames_valid: int
    status: AnalysisStatus
    reject_reason: str | None
    rep_count: int | None
    clip_score: float | None


class AnalysisDetailOut(AnalysisOut):
    reps: list[RepOut]
    corrections: list[CorrectionOut]


class HealthOut(BaseModel):
    status: str
    exercises: int
    form_profiles: int
    has_profile: bool
