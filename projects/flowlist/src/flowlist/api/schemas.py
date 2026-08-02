"""Request/response schemas for the HTTP surface (FR-13).

Pydantic v2 throughout.  Domain models are reused wherever the wire shape is
the domain shape (``Transition``, ``CoverageReport``, ``ReorderRun``); the
classes here exist only where HTTP needs a different envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flowlist.engine.models import (
    ArcProfile,
    AudioFeatures,
    CoverageReport,
    FeatureSnapshot,
    FlowReport,
    Playlist,
    PlaylistSource,
    ReorderParams,
    ReorderRun,
    Track,
    Transition,
    TransitionWeights,
)


class ErrorDetail(BaseModel):
    """Body of every 4xx the domain raises (FR-13's structured detail)."""

    code: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class ImportRequest(BaseModel):
    """``POST /playlists/import`` (FR-1, FR-2).

    Exactly one of ``content`` (inline CSV/JSON text) or ``path`` (a local
    file or directory) must be given.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    format: PlaylistSource | None = None
    content: str | None = None
    path: str | None = None
    replace: bool = False
    force: bool = False


class ImportIssueOut(BaseModel):
    line: int | None = None
    code: str
    message: str
    skipped: bool = False


class EntryOut(BaseModel):
    """One playlist entry with its track and resolved features."""

    entry_id: str
    position: int
    track: Track
    features: FeatureSnapshot
    feature_sources: dict[str, str] = Field(default_factory=dict)


class PlaylistSummaryOut(BaseModel):
    id: str
    name: str
    source: PlaylistSource
    source_ref: str | None = None
    applied_run_id: str | None = None
    created_at: datetime
    entries: int

    @classmethod
    def of(cls, playlist: Playlist, entries: int) -> PlaylistSummaryOut:
        return cls(**playlist.model_dump(), entries=entries)


class PlaylistOut(PlaylistSummaryOut):
    """``GET /playlists/{id}``: the playlist with entries and features."""

    tracks: list[EntryOut] = Field(default_factory=list)
    coverage: CoverageReport


class ImportResponse(BaseModel):
    playlist: PlaylistSummaryOut
    replaced: bool = False
    warnings: list[ImportIssueOut] = Field(default_factory=list)
    skipped: list[ImportIssueOut] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """``POST /playlists/{id}/analyze`` (FR-3)."""

    model_config = ConfigDict(extra="forbid")

    providers: list[str] | None = None
    catalog: str | None = None
    local: bool = False


class TrackOut(BaseModel):
    """``GET /tracks/{id}``: catalog row plus every stored feature source."""

    track: Track
    features: list[AudioFeatures] = Field(default_factory=list)
    resolved: FeatureSnapshot
    feature_sources: dict[str, str] = Field(default_factory=dict)


class ManualFeaturesRequest(BaseModel):
    """``PUT /tracks/{id}/features`` (FR-4).

    ``key`` accepts a Camelot code (``8A``) or a musical name (``Am``,
    ``F#m``, ``C major``); it sets ``key_pc`` and ``mode`` together.
    """

    model_config = ConfigDict(extra="forbid")

    bpm: float | None = None
    key: str | None = None
    energy: float | None = None
    danceability: float | None = None
    loudness_db: float | None = None
    valence: float | None = None


class ScoreRequest(BaseModel):
    """``POST /playlists/{id}/score`` (FR-7)."""

    model_config = ConfigDict(extra="forbid")

    weights: dict[str, float] | None = None
    profile: ArcProfile = ArcProfile.NEUTRAL


class FlowReportOut(BaseModel):
    order: list[str]
    total: float
    mean: float
    min_score: float
    seamless: int
    cliffs: int
    weights: TransitionWeights
    profile: ArcProfile
    transitions: list[Transition]

    @classmethod
    def of(
        cls, report: FlowReport, weights: TransitionWeights, profile: ArcProfile
    ) -> FlowReportOut:
        return cls(
            order=report.order,
            total=report.total,
            mean=report.mean,
            min_score=report.min_score,
            seamless=report.seamless,
            cliffs=report.cliffs,
            weights=weights,
            profile=profile,
            transitions=report.transitions,
        )


class ReorderRequest(BaseModel):
    """``POST /playlists/{id}/reorder`` — the ``ReorderParams`` body (FR-8/9/10)."""

    model_config = ConfigDict(extra="forbid")

    seed: int = 0
    weights: dict[str, float] | None = None
    profile: ArcProfile = ArcProfile.NEUTRAL
    start_entry: str | None = None
    end_entry: str | None = None
    max_passes: int = Field(default=50, ge=1)
    apply: bool = False


class RunEntryOut(BaseModel):
    position: int
    entry_id: str
    track: Track | None = None
    transition: Transition | None = None


class RunOut(BaseModel):
    """``GET /runs/{id}``: the run with entries and per-transition breakdowns."""

    run: ReorderRun
    params: ReorderParams
    applied: bool = False
    entries: list[RunEntryOut] = Field(default_factory=list)


class RunSummaryOut(BaseModel):
    id: str
    playlist_id: str
    created_at: datetime
    seed: int
    algorithm: str
    profile: ArcProfile
    score_mean_before: float
    score_mean_after: float
    applied: bool = False


__all__ = [
    "AnalyzeRequest",
    "EntryOut",
    "ErrorDetail",
    "FlowReportOut",
    "HealthResponse",
    "ImportIssueOut",
    "ImportRequest",
    "ImportResponse",
    "ManualFeaturesRequest",
    "PlaylistOut",
    "PlaylistSummaryOut",
    "ReorderRequest",
    "RunEntryOut",
    "RunOut",
    "RunSummaryOut",
    "ScoreRequest",
    "TrackOut",
]
