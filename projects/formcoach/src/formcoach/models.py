"""Pydantic v2 domain models for FormCoach.

Every entity named in ``docs/DATA_MODEL.md`` lives here, with its stated
invariants enforced by validators.  Nothing in this module reads the clock, the
filesystem or the network: timestamps are caller-supplied ISO-8601 strings and
committed datasets are handed in as models.

Coordinate/unit conventions (DATA_MODEL "Units and coordinate conventions"):

* mass is canonically kilograms; ``UserProfile.unit`` is display/input only;
* keypoints are normalized to ``[0, 1]``, origin top-left, ``y`` increasing
  **downward**;
* effective sets = 1.0 per set per primary muscle, 0.5 per secondary muscle.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

# --------------------------------------------------------------------------- units

LB_PER_KG = 2.20462262185


def lb_to_kg(value: float) -> float:
    """Convert pounds to kilograms (storage is canonically kg)."""
    return value / LB_PER_KG


def kg_to_lb(value: float) -> float:
    """Convert kilograms to pounds (display only)."""
    return value * LB_PER_KG


# ---------------------------------------------------------------------- enumerations


class Goal(StrEnum):
    HYPERTROPHY = "hypertrophy"
    STRENGTH = "strength"
    GENERAL = "general"


class Experience(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class Muscle(StrEnum):
    CHEST = "chest"
    FRONT_DELTS = "front_delts"
    SIDE_DELTS = "side_delts"
    REAR_DELTS = "rear_delts"
    LATS = "lats"
    UPPER_BACK = "upper_back"
    LOWER_BACK = "lower_back"
    BICEPS = "biceps"
    TRICEPS = "triceps"
    FOREARMS = "forearms"
    QUADS = "quads"
    HAMSTRINGS = "hamstrings"
    GLUTES = "glutes"
    CALVES = "calves"
    ABS = "abs"


#: All fifteen muscles, in DATA_MODEL order.  FR-4 reports cover exactly these.
MUSCLES: tuple[Muscle, ...] = tuple(Muscle)

#: FR-6 note (iii): an exercise is "lower body" iff every primary muscle is here.
LOWER_BODY_MUSCLES: frozenset[Muscle] = frozenset(
    {Muscle.QUADS, Muscle.HAMSTRINGS, Muscle.GLUTES, Muscle.CALVES, Muscle.LOWER_BACK}
)


class Equipment(StrEnum):
    BARBELL = "barbell"
    DUMBBELL = "dumbbell"
    MACHINE = "machine"
    CABLE = "cable"
    BODYWEIGHT = "bodyweight"
    BAND = "band"
    KETTLEBELL = "kettlebell"


class MovementPattern(StrEnum):
    SQUAT = "squat"
    HINGE = "hinge"
    HORIZONTAL_PUSH = "horizontal_push"
    HORIZONTAL_PULL = "horizontal_pull"
    VERTICAL_PUSH = "vertical_push"
    VERTICAL_PULL = "vertical_pull"
    LUNGE = "lunge"
    ISOLATION = "isolation"
    CARRY = "carry"
    CORE = "core"


class Mechanics(StrEnum):
    COMPOUND = "compound"
    ISOLATION = "isolation"


class View(StrEnum):
    """The *resolved* camera view stored on a :class:`FormAnalysis`."""

    SIDE_LEFT = "side_left"
    SIDE_RIGHT = "side_right"
    FRONT = "front"


class DeclaredView(StrEnum):
    """What a caller may declare.  ``side`` is input-only; FR-7 resolves it."""

    SIDE_LEFT = "side_left"
    SIDE_RIGHT = "side_right"
    FRONT = "front"
    SIDE = "side"


SIDE_VIEWS: frozenset[View] = frozenset({View.SIDE_LEFT, View.SIDE_RIGHT})


class Phase(StrEnum):
    BOTTOM = "bottom"
    TOP = "top"
    ASCENT_EARLY = "ascent_early"
    WHOLE_REP = "whole_rep"


class Severity(StrEnum):
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"


#: FR-10 penalties and the correction-list ordering rank.
SEVERITY_PENALTY: dict[Severity, int] = {
    Severity.MAJOR: 25,
    Severity.MODERATE: 15,
    Severity.MINOR: 8,
}
SEVERITY_RANK: dict[Severity, int] = {
    Severity.MAJOR: 3,
    Severity.MODERATE: 2,
    Severity.MINOR: 1,
}


class AnalysisKind(StrEnum):
    CLIP = "clip"
    PHOTO = "photo"


class AnalysisStatus(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"


class FindingStatus(StrEnum):
    OK = "ok"
    FAULT = "fault"
    NOT_ASSESSED = "not_assessed"


class NotAssessedReason(StrEnum):
    VIEW_MISMATCH = "view_mismatch"
    KEYPOINTS_NOT_VISIBLE = "keypoints_not_visible"
    PHASE_NOT_SHOWN = "phase_not_shown"
    NEEDS_MULTI_FRAME = "needs_multi_frame"


class ProgressionAction(StrEnum):
    INCREASE_LOAD = "increase_load"
    DECREASE_LOAD = "decrease_load"
    HOLD = "hold"
    DELOAD_RECOMMEND = "deload_recommend"
    ADD_SET = "add_set"
    ADD_REPS = "add_reps"
    PROGRESS_VARIATION = "progress_variation"


class Unit(StrEnum):
    KG = "kg"
    LB = "lb"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class MediaSource(StrEnum):
    LOCAL = "local"
    URL = "url"


class ProgramStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RepDirection(StrEnum):
    """Whether a rep starts by descending (squat, push-up) or ascending (deadlift)."""

    DOWN_UP = "down_up"
    UP_DOWN = "up_down"


class Comparator(StrEnum):
    GT = "gt"
    LT = "lt"
    ABS_GT = "abs_gt"


class PoseSource(StrEnum):
    FIXTURE = "fixture"
    MEDIAPIPE = "mediapipe"


class VolumeBand(StrEnum):
    BELOW_MEV = "below_mev"
    MEV_MAV = "mev_mav"
    MAV_MRV = "mav_mrv"
    ABOVE_MRV = "above_mrv"


class SplitName(StrEnum):
    FULL_BODY = "full_body"
    UPPER_LOWER = "upper_lower"
    UPPER_LOWER_PPL = "upper_lower_ppl"
    PPL_X2 = "ppl_x2"


# ------------------------------------------------------------------------ profile


class UserProfile(BaseModel):
    """FR-1 — the single local profile (SQLite ``user_profile``, ``id = 1``)."""

    model_config = ConfigDict(validate_assignment=True)

    id: int = Field(default=1, ge=1, le=1)
    goal: Goal
    experience: Experience
    days_per_week: int = Field(ge=2, le=6)
    equipment: list[Equipment] = Field(min_length=1)
    emphasized_muscles: list[Muscle] = Field(default_factory=list, max_length=3)
    unit: Unit = Unit.KG
    disclaimer_acknowledged_at: str | None = None
    pain_flags: list[str] = Field(default_factory=list)
    updated_at: str

    @field_validator("equipment", "emphasized_muscles", "pain_flags")
    @classmethod
    def _unique(cls, value: list) -> list:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value


# ------------------------------------------------------------------ exercise library


class Exercise(BaseModel):
    """Committed ``data/exercises.json`` row.  Read-only at runtime."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    primary_muscles: list[Muscle] = Field(min_length=1)
    secondary_muscles: list[Muscle] = Field(default_factory=list)
    equipment: list[Equipment] = Field(min_length=1)
    pattern: MovementPattern
    mechanics: Mechanics
    difficulty: int = Field(ge=1, le=3)
    rest_s: int = Field(ge=15, le=600)
    harder_variant_id: str | None = None
    instructions: list[str] = Field(default_factory=list)
    cues: list[str] = Field(default_factory=list)
    form_profile_id: str | None = None

    @model_validator(mode="after")
    def _disjoint_muscles(self) -> Exercise:
        overlap = set(self.primary_muscles) & set(self.secondary_muscles)
        if overlap:
            raise ValueError(f"{self.id}: secondary muscles overlap primary: {sorted(overlap)}")
        if len(set(self.primary_muscles)) != len(self.primary_muscles):
            raise ValueError(f"{self.id}: duplicate primary muscles")
        if len(set(self.secondary_muscles)) != len(self.secondary_muscles):
            raise ValueError(f"{self.id}: duplicate secondary muscles")
        return self

    @property
    def is_analyzable(self) -> bool:
        return self.form_profile_id is not None

    def effective_credit(self, muscle: Muscle) -> float:
        """Effective sets contributed per direct set (SCOPE § Conventions)."""
        if muscle in self.primary_muscles:
            return 1.0
        if muscle in self.secondary_muscles:
            return 0.5
        return 0.0


_ATTRIBUTION_REQUIRED_PREFIXES = ("CC-BY",)


class MediaAsset(BaseModel):
    """Committed ``data/media_manifest.json`` row — a *reference*, never bytes."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    exercise_id: str = Field(min_length=1)
    kind: MediaKind
    source: MediaSource
    ref: str = Field(min_length=1)
    license: str = Field(min_length=1)
    attribution: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> MediaAsset:
        if self.source is MediaSource.URL and not self.ref.startswith("https://"):
            raise ValueError(f"{self.id}: url assets need a well-formed https ref")
        if self.source is MediaSource.LOCAL and (
            self.ref.startswith("/") or ".." in self.ref.split("/")
        ):
            raise ValueError(f"{self.id}: local refs are relative paths under data/")
        needs_attr = self.license.upper().startswith(_ATTRIBUTION_REQUIRED_PREFIXES)
        if needs_attr and not (self.attribution or "").strip():
            raise ValueError(f"{self.id}: license {self.license} requires attribution")
        return self


class VolumeLandmark(BaseModel):
    """Weekly effective-set bands per muscle (``data/volume_landmarks.json``)."""

    model_config = ConfigDict(frozen=True)

    muscle: Muscle
    mv: int = Field(ge=0)
    mev: int = Field(ge=0)
    mav: int = Field(ge=0)
    mrv: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordering(self) -> VolumeLandmark:
        if not (self.mv <= self.mev < self.mav <= self.mrv):
            raise ValueError(f"{self.muscle}: landmarks must satisfy mv <= mev < mav <= mrv")
        return self

    def band(self, effective_sets: float) -> VolumeBand:
        if effective_sets < self.mev:
            return VolumeBand.BELOW_MEV
        if effective_sets <= self.mav:
            return VolumeBand.MEV_MAV
        if effective_sets <= self.mrv:
            return VolumeBand.MAV_MRV
        return VolumeBand.ABOVE_MRV


class TargetMusclePolicy(BaseModel):
    """FR-3 step 2 policy (``data/target_muscles.json``)."""

    model_config = ConfigDict(frozen=True)

    priority_by_goal: dict[Goal, list[Muscle]]
    count_by_days: dict[int, int]

    @model_validator(mode="after")
    def _complete(self) -> TargetMusclePolicy:
        for goal in Goal:
            order = self.priority_by_goal.get(goal)
            if not order:
                raise ValueError(f"missing priority order for goal {goal}")
            if len(set(order)) != len(order):
                raise ValueError(f"duplicate muscle in priority order for {goal}")
        for days in range(2, 7):
            if days not in self.count_by_days:
                raise ValueError(f"count_by_days missing {days}")
        return self

    def priority_index(self, goal: Goal, muscle: Muscle) -> int:
        order = self.priority_by_goal[goal]
        return order.index(muscle) if muscle in order else len(order)


class SplitSessionTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    required_patterns: list[MovementPattern] = Field(min_length=1)


class SplitTemplate(BaseModel):
    """``data/split_templates.json`` — split → sessions → required patterns.

    A template may serve several ``days_per_week`` values (``full_body`` covers
    2 and 3).  In that case the first ``days_per_week`` sessions are used.
    """

    model_config = ConfigDict(frozen=True)

    split: SplitName
    days: list[int] = Field(min_length=1)
    sessions: list[SplitSessionTemplate] = Field(min_length=1)

    @model_validator(mode="after")
    def _enough_sessions(self) -> SplitTemplate:
        if len(self.sessions) < max(self.days):
            raise ValueError(f"{self.split}: needs >= {max(self.days)} session templates")
        return self


class IncrementPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    kg: float | None = Field(default=None, gt=0)
    lb: float | None = Field(default=None, gt=0)


# ---------------------------------------------------------------------- form profiles


class FaultRule(BaseModel):
    """One rule of a :class:`FormProfile` (SCOPE § Fault catalog)."""

    model_config = ConfigDict(frozen=True)

    fault_id: str = Field(min_length=1)
    views: list[View] = Field(min_length=1)
    feature: str = Field(min_length=1)
    feature_keypoints: list[str] = Field(min_length=1)
    phase: Phase
    comparator: Comparator
    threshold: float
    severity: Severity
    cue: str = Field(min_length=1)
    citation: str | None = None

    def violated_by(self, measured: float) -> bool:
        if self.comparator is Comparator.GT:
            return measured > self.threshold
        if self.comparator is Comparator.LT:
            return measured < self.threshold
        return abs(measured) > abs(self.threshold)


class FormProfile(BaseModel):
    """``data/form_profiles/<exercise>.json``."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    exercise_id: str = Field(min_length=1)
    primary_signal: str = Field(min_length=1)
    direction: RepDirection
    min_rep_duration_s: float = Field(default=0.8, gt=0)
    prominence_frac: float = Field(default=0.15, gt=0, lt=1)
    smoothing_window_frac: float = Field(default=0.25, gt=0, lt=2)
    required_keypoints: dict[View, list[str]]
    rules: list[FaultRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_fault_ids(self) -> FormProfile:
        ids = [r.fault_id for r in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.id}: duplicate fault_id in profile")
        for view in View:
            if view not in self.required_keypoints:
                raise ValueError(f"{self.id}: required_keypoints missing view {view}")
        return self

    def rule(self, fault_id: str) -> FaultRule:
        for r in self.rules:
            if r.fault_id == fault_id:
                return r
        raise KeyError(fault_id)


# ------------------------------------------------------------------------ pose data


class Keypoint(BaseModel):
    """One COCO-17 keypoint in normalized image coordinates (y grows downward)."""

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    conf: float = Field(ge=0.0, le=1.0)

    @property
    def in_frame(self) -> bool:
        return 0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0


class PoseFrame(BaseModel):
    model_config = ConfigDict(frozen=True)

    t_ms: float = Field(ge=0)
    keypoints: dict[str, Keypoint]


class PoseSequence(BaseModel):
    """FR-7 output of a :class:`~formcoach.adapters.pose.PoseEstimator`."""

    model_config = ConfigDict(frozen=True)

    fps: float = Field(gt=0)
    frames: list[PoseFrame] = Field(min_length=1)
    declared_view: DeclaredView | None = None
    source_ref: str = ""
    pose_source: PoseSource = PoseSource.FIXTURE

    @property
    def frame_count(self) -> int:
        return len(self.frames)


class RepBoundary(BaseModel):
    """FR-8 output: one segmented repetition."""

    model_config = ConfigDict(frozen=True)

    rep_index: int = Field(ge=0)
    start_frame: int = Field(ge=0)
    extremum_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> RepBoundary:
        if not (self.start_frame <= self.extremum_frame <= self.end_frame):
            raise ValueError("rep frames must satisfy start <= extremum <= end")
        return self


class ViewResolution(BaseModel):
    """FR-7 view resolution result."""

    model_config = ConfigDict(frozen=True)

    view: View
    view_inferred: bool


class ScreeningResult(BaseModel):
    """FR-7 visibility screening result."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    reject_reason: str | None = None
    frames_total: int = Field(ge=0)
    frames_valid: int = Field(ge=0)
    low_confidence_frac: float = Field(ge=0.0, le=1.0)
    out_of_frame_frac: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _reason_iff_rejected(self) -> ScreeningResult:
        if self.accepted != (self.reject_reason is None):
            raise ValueError("reject_reason must be set iff the clip is rejected")
        return self


# -------------------------------------------------------------------------- programs


class Prescription(BaseModel):
    """One prescribed exercise inside a :class:`ProgramSession`."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    session_id: int | None = None
    position: int = Field(ge=0)
    exercise_id: str
    sets: int = Field(ge=1, le=10)
    rep_low: int = Field(ge=1)
    rep_high: int = Field(ge=1)
    target_rir: float = Field(ge=0)
    rest_s: int = Field(ge=15, le=600)
    load_note: str | None = None

    @model_validator(mode="after")
    def _rep_order(self) -> Prescription:
        if self.rep_low > self.rep_high:
            raise ValueError("rep_low must be <= rep_high")
        return self


class ProgramSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    program_id: int | None = None
    week: int = Field(ge=1, le=5)
    day_index: int = Field(ge=0)
    name: str


class Program(BaseModel):
    """Immutable mesocycle snapshot of the profile it was generated from."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    created_at: str
    seed: int
    goal: Goal
    experience: Experience
    days_per_week: int = Field(ge=2, le=6)
    emphasized_muscles: list[Muscle] = Field(default_factory=list)
    equipment: list[Equipment] = Field(min_length=1)
    target_muscles: list[Muscle] = Field(min_length=1)
    weeks: int = Field(default=5, ge=1)
    split: SplitName
    status: ProgramStatus = ProgramStatus.ACTIVE
    #: muscle -> the FR-3 step-3 weekly effective-set plan, one entry per week.
    #: Snapshotted for the same reason as ``target_muscles``: so reports and
    #: evals can read the plan (M5 constraint 14) without recomputing it.
    weekly_set_targets: dict[Muscle, list[int]] = Field(default_factory=dict)


class SessionPlan(BaseModel):
    """A generated session together with its prescriptions (pre-persistence)."""

    model_config = ConfigDict(frozen=True)

    week: int = Field(ge=1, le=5)
    day_index: int = Field(ge=0)
    name: str
    prescriptions: list[Prescription]


class ProgramPlan(BaseModel):
    """FR-3 return value: an unsaved program plus its sessions."""

    model_config = ConfigDict(frozen=True)

    program: Program
    sessions: list[SessionPlan]

    def session(self, week: int, day_index: int) -> SessionPlan:
        for s in self.sessions:
            if s.week == week and s.day_index == day_index:
                return s
        raise KeyError((week, day_index))


class NextPrescription(BaseModel):
    """FR-6 return type — derived at read time, never stored."""

    model_config = ConfigDict(frozen=True)

    exercise_id: str
    position: int = Field(ge=0)
    sets: int = Field(ge=1)
    rep_low: int = Field(ge=1)
    rep_high: int = Field(ge=1)
    target_rir: float = Field(ge=0)
    rest_s: int = Field(ge=15, le=600)
    load_note: str | None = None
    action: ProgressionAction
    clause: str
    suggested_load_kg: float | None = None
    target_reps: int = Field(ge=1)
    substituted_for: str | None = None
    dropped_reason: str | None = None
    rationale: str


# ---------------------------------------------------------------------------- logs


class SetLog(BaseModel):
    """Append-only set record.  ``e1rm_kg`` is derived, never supplied."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    workout_id: int | None = None
    exercise_id: str = Field(min_length=1)
    set_index: int = Field(ge=0)
    weight_kg: float = Field(ge=0.0)
    reps: int = Field(ge=1)
    rir: float | None = Field(default=None, ge=0.0, le=10.0)
    pain_flag: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def e1rm_kg(self) -> float | None:
        """RIR-adjusted Epley, only for loaded *and* rated sets (FR-5)."""
        if self.weight_kg > 0.0 and self.rir is not None:
            return round(self.weight_kg * (1.0 + (self.reps + self.rir) / 30.0), 6)
        return None


class WorkoutLog(BaseModel):
    """Append-only workout header.  Pain is recorded per set, never here."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    performed_at: str
    program_session_id: int | None = None
    notes: str | None = None


class LoggedSession(BaseModel):
    """A workout plus its sets — the engine's view of history (FR-6, FR-4)."""

    model_config = ConfigDict(frozen=True)

    workout: WorkoutLog
    sets: list[SetLog]

    def sets_for(self, exercise_id: str) -> list[SetLog]:
        return [s for s in self.sets if s.exercise_id == exercise_id]


# ------------------------------------------------------------------ volume reporting


class MuscleVolumeRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    muscle: Muscle
    effective_sets: float = Field(ge=0.0)
    direct_sets: int = Field(ge=0)
    band: VolumeBand
    is_target: bool
    mv: int
    mev: int
    mav: int
    mrv: int


class VolumeReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    iso_week: str
    rows: list[MuscleVolumeRow]

    def row(self, muscle: Muscle) -> MuscleVolumeRow:
        for r in self.rows:
            if r.muscle is muscle:
                return r
        raise KeyError(muscle)


# ------------------------------------------------------------------- form analysis


class FaultFinding(BaseModel):
    """Exactly one row per profile rule per rep (complete-matrix invariant)."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    rep_analysis_id: int | None = None
    fault_id: str
    status: FindingStatus
    not_assessed_reason: NotAssessedReason | None = None
    measured: float | None = None
    threshold: float
    severity: Severity
    frame: int | None = None
    cue: str | None = None

    @model_validator(mode="after")
    def _iff_invariants(self) -> FaultFinding:
        not_assessed = self.status is FindingStatus.NOT_ASSESSED
        if not_assessed != (self.not_assessed_reason is not None):
            raise ValueError("not_assessed_reason must be set iff status == not_assessed")
        if not_assessed != (self.measured is None):
            raise ValueError("measured must be null iff status == not_assessed")
        if not_assessed and self.frame is not None:
            raise ValueError("frame must be null when the rule was not assessed")
        if (self.status is FindingStatus.FAULT) != (self.cue is not None):
            raise ValueError("cue must be set iff status == fault")
        return self


class RepAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    analysis_id: int | None = None
    rep_index: int = Field(ge=0)
    start_frame: int = Field(ge=0)
    extremum_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    metrics: dict[str, float] = Field(default_factory=dict)
    score: float = Field(ge=0.0, le=100.0)
    findings: list[FaultFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> RepAnalysis:
        if not (self.start_frame <= self.extremum_frame <= self.end_frame):
            raise ValueError("rep frames must satisfy start <= extremum <= end")
        return self


class FormAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    created_at: str
    analysis_kind: AnalysisKind
    exercise_id: str
    form_profile_id: str
    source_ref: str
    pose_source: PoseSource
    view: View
    view_inferred: bool
    declared_phase: Phase | None = None
    fps: float | None = None
    frames_total: int = Field(ge=0)
    frames_valid: int = Field(ge=0)
    status: AnalysisStatus
    reject_reason: str | None = None
    rep_count: int | None = None
    clip_score: float | None = None

    @model_validator(mode="after")
    def _iff_invariants(self) -> FormAnalysis:
        rejected = self.status is AnalysisStatus.REJECTED
        if rejected != (self.reject_reason is not None):
            raise ValueError("reject_reason must be set iff status == rejected")
        if rejected != (self.rep_count is None):
            raise ValueError("rep_count must be null iff status == rejected")
        if rejected != (self.clip_score is None):
            raise ValueError("clip_score must be null iff status == rejected")
        is_photo = self.analysis_kind is AnalysisKind.PHOTO
        if is_photo != (self.declared_phase is not None):
            raise ValueError("declared_phase must be set iff analysis_kind == photo")
        if is_photo and self.fps is not None:
            raise ValueError("fps must be null for photo analyses")
        return self


class Correction(BaseModel):
    """One entry of the FR-10 prioritized correction list."""

    model_config = ConfigDict(frozen=True)

    fault_id: str
    severity: Severity
    occurrences: int = Field(ge=1)
    cue: str


class AnalysisResult(BaseModel):
    """Everything FR-10/FR-15 produce for one submitted clip or photo."""

    model_config = ConfigDict(frozen=True)

    analysis: FormAnalysis
    reps: list[RepAnalysis] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)


# ----------------------------------------------------------------------- datasets


class Datasets(BaseModel):
    """All committed datasets, loaded by ``store.datasets`` and passed in.

    The engine only ever sees this object — it never touches the filesystem.
    """

    model_config = ConfigDict(frozen=True)

    exercises: list[Exercise]
    media_assets: list[MediaAsset]
    landmarks: dict[Muscle, VolumeLandmark]
    target_policy: TargetMusclePolicy
    split_templates: list[SplitTemplate]
    load_increments: dict[Equipment, IncrementPair]
    form_profiles: dict[str, FormProfile]

    def exercise(self, exercise_id: str) -> Exercise:
        for e in self.exercises:
            if e.id == exercise_id:
                return e
        raise KeyError(exercise_id)

    def find_exercise(self, token: str) -> Exercise | None:
        """Resolve by id first, then by (case-insensitive) alias or name."""
        needle = token.strip().lower()
        for e in self.exercises:
            if e.id.lower() == needle:
                return e
        for e in self.exercises:
            if e.name.lower() == needle or needle in {a.lower() for a in e.aliases}:
                return e
        return None

    def split_for(self, split: SplitName) -> SplitTemplate:
        for t in self.split_templates:
            if t.split is split:
                return t
        raise KeyError(split)

    def assets_for(self, exercise_id: str) -> list[MediaAsset]:
        return [a for a in self.media_assets if a.exercise_id == exercise_id]

    def increment_kg(self, exercise: Exercise) -> float | None:
        """Smallest non-null kg increment among the exercise's equipment."""
        values = [
            self.load_increments[eq].kg
            for eq in exercise.equipment
            if eq in self.load_increments and self.load_increments[eq].kg is not None
        ]
        return min(values) if values else None
