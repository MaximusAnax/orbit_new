"""Pydantic v2 domain models (DATA_MODEL.md).

Everything the product persists or returns is declared here so the invariants
live in one place.  The engine never reads a clock: every timestamp is a caller
supplied ``datetime``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flowlist.errors import InvalidWeightsError

# --------------------------------------------------------------------------- #
# Shared constants
# --------------------------------------------------------------------------- #

#: A transition at or above this total score is reported as "seamless" (D12).
SEAMLESS_THRESHOLD = 0.70
#: A transition below this total score is reported as a "cliff" (D12).
CLIFF_THRESHOLD = 0.40
#: Score a component contributes when its inputs are missing (D10).
NEUTRAL_COMPONENT_SCORE = 0.5
#: Decimal places every component/total score is rounded to before any
#: comparison or tie-break, so near-ties are stable (D6, FR-15).
SCORE_PRECISION = 12
#: Optimizer input cap (FR-8).
MAX_PLAYLIST_SIZE = 500
#: Held-Karp guard (FR-8).
MAX_EXACT_SIZE = 14

BPM_MIN = 40.0
BPM_MAX = 260.0

#: The five transition components, in canonical order.  Iteration order is
#: fixed so flag lists and JSON dumps are deterministic.
COMPONENT_NAMES: tuple[str, ...] = ("key", "bpm", "energy", "loudness", "danceability")


def round_score(value: float) -> float:
    """Round to :data:`SCORE_PRECISION` decimals (D6 tie-break stability)."""
    return round(value, SCORE_PRECISION)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class FeatureSource(StrEnum):
    """Provenance of an :class:`AudioFeatures` row (DATA_MODEL 2.2)."""

    MANUAL = "manual"
    LOCAL_ANALYSIS = "local_analysis"
    STREAMING = "streaming"
    IMPORT = "import"
    FIXTURE = "fixture"


#: Default read-time precedence for feature resolution (FR-3).
DEFAULT_PRECEDENCE: tuple[FeatureSource, ...] = (
    FeatureSource.MANUAL,
    FeatureSource.LOCAL_ANALYSIS,
    FeatureSource.STREAMING,
    FeatureSource.IMPORT,
    FeatureSource.FIXTURE,
)


class PlaylistSource(StrEnum):
    CSV = "csv"
    JSON = "json"
    DIRECTORY = "directory"
    MANUAL = "manual"


class Algorithm(StrEnum):
    GREEDY_2OPT = "greedy_2opt"
    ORTOOLS = "ortools"


class ArcProfile(StrEnum):
    """Directional energy bias applied to the energy component (FR-10, D4)."""

    NEUTRAL = "neutral"
    BUILD = "build"
    COOL = "cool"


class KeyRelation(StrEnum):
    """The named DJ relations of the Camelot wheel (D2)."""

    SAME_KEY = "same_key"
    RELATIVE = "relative"
    ADJACENT_FIFTH = "adjacent_fifth"
    DIAGONAL = "diagonal"
    ENERGY_BOOST = "energy_boost"
    ENERGY_DROP = "energy_drop"
    PARALLEL = "parallel"
    SEMITONE_LIFT = "semitone_lift"
    CLASH = "clash"
    #: Not a wheel relation: at least one side has no key (D10).
    UNKNOWN = "unknown"


class ExportFormat(StrEnum):
    M3U = "m3u"
    CSV = "csv"
    JSON = "json"


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #

Bpm = Annotated[float, Field(ge=BPM_MIN, le=BPM_MAX)]
PitchClass = Annotated[int, Field(ge=0, le=11)]
Mode = Annotated[int, Field(ge=0, le=1)]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]
LoudnessDb = Annotated[float, Field(ge=-60.0, le=0.0)]


class FeatureSnapshot(BaseModel):
    """The six musical facts scoring consumes.

    Also the shape stored in ``Transition.features_from/to`` so a run stays
    self-contained after features are re-analysed (DATA_MODEL 2.6).
    """

    model_config = ConfigDict(frozen=True)

    bpm: Bpm | None = None
    key_pc: PitchClass | None = None
    mode: Mode | None = None
    energy: Unit | None = None
    danceability: Unit | None = None
    loudness_db: LoudnessDb | None = None

    @model_validator(mode="after")
    def _key_and_mode_set_together(self) -> FeatureSnapshot:
        if (self.key_pc is None) != (self.mode is None):
            raise ValueError("key_pc and mode must be set or null together (DATA_MODEL 2.2)")
        return self

    @property
    def has_key(self) -> bool:
        return self.key_pc is not None and self.mode is not None


class AudioFeatures(FeatureSnapshot):
    """One stored row per (track, source) — DATA_MODEL 2.2."""

    track_id: str = Field(min_length=1)
    source: FeatureSource
    valence: Unit | None = None
    confidence: Unit = 1.0
    analyzed_at: datetime

    def snapshot(self) -> FeatureSnapshot:
        return FeatureSnapshot(
            bpm=self.bpm,
            key_pc=self.key_pc,
            mode=self.mode,
            energy=self.energy,
            danceability=self.danceability,
            loudness_db=self.loudness_db,
        )


class ResolvedFeatures(FeatureSnapshot):
    """Field-wise merge across sources (FR-3).

    Not persisted: DATA_MODEL 2.2 makes resolution a pure read-time policy, so
    ``field_sources`` records which source won each field for the coverage
    report and for ``explain``.
    """

    track_id: str | None = None
    valence: Unit | None = None
    confidence: Unit = 1.0
    field_sources: dict[str, FeatureSource] = Field(default_factory=dict)

    def snapshot(self) -> FeatureSnapshot:
        return FeatureSnapshot(
            bpm=self.bpm,
            key_pc=self.key_pc,
            mode=self.mode,
            energy=self.energy,
            danceability=self.danceability,
            loudness_db=self.loudness_db,
        )


# --------------------------------------------------------------------------- #
# Catalog / playlist entities
# --------------------------------------------------------------------------- #


class Track(BaseModel):
    """A song in the catalog, deduplicated across playlists (DATA_MODEL 2.1)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str
    artist: str
    album: str | None = None
    duration_ms: Annotated[int, Field(gt=0)] | None = None
    spotify_id: str | None = None
    file_path: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _identifiable(self) -> Track:
        if not (self.spotify_id or self.file_path or self.title.strip() or self.artist.strip()):
            raise ValueError("a Track needs at least one of spotify_id, file_path or title/artist")
        return self

    @property
    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


class Playlist(BaseModel):
    """DATA_MODEL 2.3."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: PlaylistSource
    source_ref: str | None = None
    applied_run_id: str | None = None
    created_at: datetime


class PlaylistEntry(BaseModel):
    """An occurrence of a track at a position — the optimizer's node (D8)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    playlist_id: str = Field(min_length=1)
    position: Annotated[int, Field(ge=0)]
    track_id: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


class TransitionWeights(BaseModel):
    """Component weights (FR-6).

    Every weight must be >= 0 and at least one > 0; the engine scores with
    :meth:`normalized`, which is what keeps ``score in [0, 1]`` and the D12
    constants meaningful for user-supplied weights.
    """

    model_config = ConfigDict(frozen=True)

    key: Annotated[float, Field(ge=0.0)] = 0.35
    bpm: Annotated[float, Field(ge=0.0)] = 0.35
    energy: Annotated[float, Field(ge=0.0)] = 0.20
    loudness: Annotated[float, Field(ge=0.0)] = 0.10
    danceability: Annotated[float, Field(ge=0.0)] = 0.0

    @model_validator(mode="after")
    def _at_least_one_positive(self) -> TransitionWeights:
        if self.total <= 0.0:
            raise ValueError("at least one transition weight must be > 0")
        return self

    @property
    def total(self) -> float:
        return self.key + self.bpm + self.energy + self.loudness + self.danceability

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in COMPONENT_NAMES}

    def normalized(self) -> TransitionWeights:
        """Rescale to sum 1.

        Idempotent: weights already summing to 1 (within float slop) are
        returned unchanged, and normalized weights are rounded to
        :data:`SCORE_PRECISION` so scaling every weight by a positive constant
        is an exact no-op (M3's weight-algebra property).
        """
        total = self.total
        if abs(total - 1.0) <= 1e-12:
            return self
        return TransitionWeights(
            **{name: round_score(value / total) for name, value in self.as_dict().items()}
        )

    @classmethod
    def parse(cls, value: TransitionWeights | dict[str, float] | None) -> TransitionWeights:
        """Build weights from user input, raising :class:`InvalidWeightsError`.

        Pydantic wraps validator failures in ``ValidationError``; the API and
        CLI need the FR-13 ``invalid_weights`` code, so user input goes through
        here rather than through the constructor.
        """
        if value is None:
            return cls()
        if isinstance(value, TransitionWeights):
            return value
        unknown = set(value) - set(COMPONENT_NAMES)
        if unknown:
            raise InvalidWeightsError(
                f"unknown weight(s): {', '.join(sorted(unknown))}",
                allowed=list(COMPONENT_NAMES),
            )
        try:
            return cls(**value)
        except (ValueError, TypeError) as exc:  # pydantic ValidationError subclasses ValueError
            raise InvalidWeightsError(
                "weights must be numeric, >= 0, and at least one > 0", weights=dict(value)
            ) from exc


class Transition(BaseModel):
    """One directed seam a -> b with its full breakdown (DATA_MODEL 2.6)."""

    model_config = ConfigDict(frozen=True)

    score: Annotated[float, Field(ge=0.0, le=1.0)]
    components: dict[str, float | None]
    weights: TransitionWeights
    key_relation: KeyRelation
    camelot_from: str | None = None
    camelot_to: str | None = None
    bpm_from: float | None = None
    bpm_to: float | None = None
    bpm_delta_pct: float | None = None
    bpm_folded: bool = False
    energy_delta: float | None = None
    loudness_delta_db: float | None = None
    features_from: FeatureSnapshot
    features_to: FeatureSnapshot
    flags: list[str] = Field(default_factory=list)
    from_id: str | None = None
    to_id: str | None = None

    @property
    def is_seamless(self) -> bool:
        return self.score >= SEAMLESS_THRESHOLD

    @property
    def is_cliff(self) -> bool:
        return self.score < CLIFF_THRESHOLD


class FlowReport(BaseModel):
    """Aggregates for one ordering (FR-7)."""

    model_config = ConfigDict(frozen=True)

    order: list[str]
    transitions: list[Transition]
    total: float
    mean: float
    min_score: float
    seamless: int
    cliffs: int

    @property
    def n_entries(self) -> int:
        return len(self.order)


class ReorderParams(BaseModel):
    """Everything that makes a reorder reproducible (FR-8/9/10/15)."""

    model_config = ConfigDict(frozen=True)

    seed: int = 0
    weights: TransitionWeights = Field(default_factory=TransitionWeights)
    profile: ArcProfile = ArcProfile.NEUTRAL
    start_entry: str | None = None
    end_entry: str | None = None
    max_passes: Annotated[int, Field(ge=1)] = 50

    @model_validator(mode="after")
    def _normalize_weights(self) -> ReorderParams:
        """Runs persist the weights the engine actually used (DATA_MODEL 2.5)."""
        normalized = self.weights.normalized()
        if normalized is not self.weights:
            object.__setattr__(self, "weights", normalized)
        return self


class ReorderResult(BaseModel):
    """Pure-engine optimizer output: indices into the score matrix (FR-8)."""

    model_config = ConfigDict(frozen=True)

    order: list[int]
    total: float
    construction_total: float
    passes: int
    moves: int
    starts: int
    seed: int
    algorithm: Algorithm = Algorithm.GREEDY_2OPT

    @property
    def improvement(self) -> float:
        return round_score(self.total - self.construction_total)


# --------------------------------------------------------------------------- #
# Coverage / import interchange
# --------------------------------------------------------------------------- #


class CoverageReport(BaseModel):
    """FR-3's return type; snapshotted onto every run (DATA_MODEL 2.5)."""

    model_config = ConfigDict(frozen=True)

    tracks: int
    full: int
    fields: list[str]
    resolved: dict[str, int] = Field(default_factory=dict)
    missing: dict[str, int] = Field(default_factory=dict)
    per_track_missing: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = [f"{self.full}/{self.tracks} tracks fully featured"]
        for name in self.fields:
            count = self.missing.get(name, 0)
            if count:
                parts.append(f"{count} missing {name}")
        return "; ".join(parts)


class ImportIssue(BaseModel):
    """A per-row note raised while reading a playlist source (FR-1)."""

    model_config = ConfigDict(frozen=True)

    line: int | None = None
    code: str
    message: str
    skipped: bool = False


class ImportedTrack(BaseModel):
    """One parsed row, before catalog identity is assigned."""

    model_config = ConfigDict(frozen=True)

    title: str
    artist: str = ""
    album: str | None = None
    duration_ms: Annotated[int, Field(gt=0)] | None = None
    spotify_id: str | None = None
    file_path: str | None = None
    file_sha1: str | None = None
    features: FeatureSnapshot | None = None


class ImportedPlaylist(BaseModel):
    """The ``PlaylistReader`` return type (SCOPE Architecture-Adapters)."""

    model_config = ConfigDict(frozen=True)

    name: str
    source: PlaylistSource
    source_ref: str | None = None
    tracks: list[ImportedTrack] = Field(default_factory=list)
    issues: list[ImportIssue] = Field(default_factory=list)

    @property
    def warnings(self) -> list[ImportIssue]:
        return [i for i in self.issues if not i.skipped]

    @property
    def skipped(self) -> list[ImportIssue]:
        return [i for i in self.issues if i.skipped]


# --------------------------------------------------------------------------- #
# Runs (append-only)
# --------------------------------------------------------------------------- #


class ReorderRun(BaseModel):
    """DATA_MODEL 2.5 — append-only, hence frozen."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    playlist_id: str = Field(min_length=1)
    created_at: datetime
    engine_version: str
    algorithm: Algorithm
    seed: int
    params: ReorderParams
    coverage: CoverageReport
    score_mean_before: float
    score_mean_after: float
    score_min_before: float
    score_min_after: float
    score_total_before: float
    score_total_after: float
    seamless_before: int
    seamless_after: int
    cliff_before: int
    cliff_after: int


class RunEntry(BaseModel):
    """DATA_MODEL 2.6 — the proposed ordering plus its stored explanation."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    position: Annotated[int, Field(ge=0)]
    entry_id: str = Field(min_length=1)
    transition: Transition | None = None

    @model_validator(mode="after")
    def _transition_null_iff_first(self) -> RunEntry:
        if (self.position == 0) != (self.transition is None):
            raise ValueError("transition must be null iff position == 0 (DATA_MODEL 2.6)")
        return self


class RunSummary(BaseModel):
    """Compact listing row for ``GET /runs`` and ``flowlist ls``."""

    model_config = ConfigDict(frozen=True)

    id: str
    playlist_id: str
    created_at: datetime
    seed: int
    algorithm: Algorithm
    score_mean_before: float
    score_mean_after: float
    applied: bool = False


class ExportRow(BaseModel):
    """One line of an exported ordering (FR-12)."""

    model_config = ConfigDict(frozen=True)

    position: int
    entry_id: str
    track: Track
    features: FeatureSnapshot | None = None
    transition: Transition | None = None


class PlaylistExport(BaseModel):
    """Everything a :class:`~flowlist.adapters.base.PlaylistWriter` needs."""

    model_config = ConfigDict(frozen=True)

    playlist_name: str
    run: ReorderRun | None = None
    rows: list[ExportRow] = Field(default_factory=list)


def validate_entry_set(entry_ids: list[str], expected: set[str]) -> None:
    """Assert a run's entry ids are a permutation of the playlist's (2.6)."""
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("run entries contain duplicates")
    if set(entry_ids) != expected:
        raise ValueError("run entries are not a permutation of the playlist's entries")


__all__ = [
    "BPM_MAX",
    "BPM_MIN",
    "CLIFF_THRESHOLD",
    "COMPONENT_NAMES",
    "DEFAULT_PRECEDENCE",
    "MAX_EXACT_SIZE",
    "MAX_PLAYLIST_SIZE",
    "NEUTRAL_COMPONENT_SCORE",
    "SCORE_PRECISION",
    "SEAMLESS_THRESHOLD",
    "Algorithm",
    "ArcProfile",
    "AudioFeatures",
    "CoverageReport",
    "ExportFormat",
    "ExportRow",
    "FeatureSnapshot",
    "FeatureSource",
    "FlowReport",
    "ImportIssue",
    "ImportedPlaylist",
    "ImportedTrack",
    "KeyRelation",
    "Playlist",
    "PlaylistEntry",
    "PlaylistExport",
    "PlaylistSource",
    "ReorderParams",
    "ReorderResult",
    "ReorderRun",
    "ResolvedFeatures",
    "RunEntry",
    "RunSummary",
    "Track",
    "Transition",
    "TransitionWeights",
    "round_score",
    "validate_entry_set",
]
