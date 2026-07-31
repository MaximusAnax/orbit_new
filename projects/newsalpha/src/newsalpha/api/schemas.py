"""Request/response schemas for the FastAPI surface (FR-12).

Pydantic v2 throughout.  Response models are projections of the domain models in
`newsalpha.models` -- the API never invents a field, and it never exposes a
buy/sell field, because none exists (US-4).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    Aggregates,
    Article,
    Asset,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    Brief,
    Digest,
    Event,
    EventLink,
    Signal,
    WatchlistItem,
)

# --------------------------------------------------------------------------- #
# Health & errors
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    version: str
    assets: int
    patterns: int
    priors: int
    templates: int


class ErrorResponse(BaseModel):
    """The one error shape every failing endpoint returns."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str = Field(description="ISO-8601 UTC instant the recompute is anchored to")
    since: str | None = None
    until: str | None = None
    feed: Literal["fixture", "rss"] = "fixture"
    path: str | None = Field(
        default=None,
        description="fixture corpus path; defaults to the committed evals corpus",
    )


class IngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str
    articles: int
    articles_excluded: int
    clusters: int
    events: int
    signals_new: int
    revisions_new: int
    briefs: int


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


class ArticleOut(Article):
    """The stored article, verbatim."""


class ArticleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    articles: list[ArticleOut]


class EventLinkOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_name: str | None = None
    role: str
    link_confidence: float
    evidence: dict[str, Any]

    @classmethod
    def of(cls, link: EventLink, asset_name: str | None) -> EventLinkOut:
        return cls(
            asset_id=link.asset_id,
            asset_name=asset_name,
            role=link.role.value,
            link_confidence=link.link_confidence,
            evidence=link.evidence.model_dump(mode="json"),
        )


class EventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    cluster_id: str
    event_type: str
    stage: str
    attributes: dict[str, Any]
    extraction_confidence: float
    evidence: list[dict[str, Any]]
    notes: list[str]
    event_date: str
    observed_at: str
    links: list[EventLinkOut] = Field(default_factory=list)

    @classmethod
    def of(cls, event: Event, links: list[EventLinkOut] | None = None) -> EventOut:
        return cls(
            id=event.id,
            cluster_id=event.cluster_id,
            event_type=event.event_type.value,
            stage=event.stage.value,
            attributes=dict(event.attributes),
            extraction_confidence=event.extraction_confidence,
            evidence=[span.model_dump(mode="json") for span in event.evidence],
            notes=list(event.notes),
            event_date=event.event_date,
            observed_at=event.observed_at,
            links=links or [],
        )


class EventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    events: list[EventOut]


class SignalOut(Signal):
    """The stored signal revision, verbatim (no imperative field exists, US-4)."""


class SignalListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    include_superseded: bool
    signals: list[SignalOut]


class RevisionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_key: str
    count: int
    revisions: list[SignalOut]


class BriefOut(Brief):
    """The persisted brief; `frame_checked` is always true by construction (FR-7)."""


class DigestResponse(Digest):
    """FR-8's ranked digest, including its explicit empty state."""


class AssetOut(Asset):
    pass


class AssetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    assets: list[AssetOut]


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    items: list[WatchlistItem]


class WatchlistMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    added: bool = False
    removed: bool = False


# --------------------------------------------------------------------------- #
# Prices & backtests
# --------------------------------------------------------------------------- #


class PricesLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["fixture", "live"] = "fixture"
    assets: list[str] | None = None
    start: str
    end: str
    directory: str | None = Field(
        default=None, description="fixture CSV directory; defaults to the committed series"
    )


class PricesLoadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    assets: int
    bars: int


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    placebo_seed: int | None = None
    as_of: str | None = None


class BacktestRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    params: BacktestParams
    as_of: str
    aggregates: Aggregates
    per_type: dict[str, Aggregates]

    @classmethod
    def of(cls, run: BacktestRun) -> BacktestRunOut:
        return cls(
            id=run.id,
            params=run.params,
            as_of=run.as_of,
            aggregates=run.aggregates,
            per_type=dict(run.per_type),
        )


class BacktestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: BacktestRunOut
    results: list[BacktestResult] = Field(default_factory=list)


class BacktestListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    runs: list[BacktestRunOut]


__all__ = [
    "ArticleListResponse",
    "ArticleOut",
    "AssetListResponse",
    "AssetOut",
    "BacktestListResponse",
    "BacktestRequest",
    "BacktestResponse",
    "BacktestRunOut",
    "BriefOut",
    "DigestResponse",
    "ErrorResponse",
    "EventLinkOut",
    "EventListResponse",
    "EventOut",
    "HealthResponse",
    "IngestRequest",
    "IngestResponse",
    "PricesLoadRequest",
    "PricesLoadResponse",
    "RevisionsResponse",
    "SignalListResponse",
    "SignalOut",
    "WatchlistMutationResponse",
    "WatchlistResponse",
]
