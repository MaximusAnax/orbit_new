"""Request and response schemas for the FastAPI surface (FR-12).

Domain entities (``Listing``, ``IndexPoint``, ``FashionEvent``, ``Garment``,
``Advice``) are already Pydantic v2 models with their invariants attached, so
they are returned as-is; the schemas here cover request bodies and the composite
responses that have no domain equivalent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    Advice,
    AdviceAction,
    Category,
    ConditionGrade,
    EventSource,
    EventType,
    FashionEvent,
    Garment,
    GarmentStatus,
    IndexPoint,
    Listing,
    ListingSource,
    ValuationMethod,
    ValuationReason,
)

__all__ = [
    "AdviceBatchResponse",
    "AdviseRequest",
    "BacktestRequest",
    "BacktestRunResponse",
    "BrandResponse",
    "EventCreateRequest",
    "EventDetailResponse",
    "EventIngestRequest",
    "EventIngestResponse",
    "GarmentCreateRequest",
    "GarmentPatchRequest",
    "HealthResponse",
    "IndexBuildRequest",
    "IndexBuildResponse",
    "IndexSeriesResponse",
    "ListingsLoadRequest",
    "ListingsLoadResponse",
    "PortfolioValuationResponse",
    "StrataResponse",
    "ValuationResponse",
]


class HealthResponse(BaseModel):
    status: str = "ok"
    config_version: str
    brands: int
    listings: int
    index_points: int
    events: int
    garments: int


class ListingsLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ListingSource = ListingSource.FIXTURE
    path: str = Field(min_length=1, description="fixture JSONL or user-exported CSV")


class ListingsLoadResponse(BaseModel):
    ingested: int
    duplicates: int
    skipped_unresolved: int
    skipped_currency: int
    skipped_invalid: int
    unresolved_refs: list[str] = Field(default_factory=list)


class IndexBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str | None = Field(
        default=None, description="ISO date; defaults to the newest listing timestamp"
    )


class IndexBuildResponse(BaseModel):
    as_of: str
    strata_built: int
    leaf_strata: int
    points_written: int
    excluded_total: int


class IndexSeriesResponse(BaseModel):
    stratum_id: str
    points: list[IndexPoint]
    excluded: dict[str, list[Listing]] = Field(
        default_factory=dict, description="week -> the listings the FR-3 fence removed"
    )


class StrataResponse(BaseModel):
    strata: list[str]


class EventIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="fixture", pattern="^(fixture|rss)$")
    path: str | None = None
    social: bool = Field(default=False, description="read a fixture file as a social feed")
    since: str = "1970-01-01"
    until: str = "2999-12-31"


class EventIngestResponse(BaseModel):
    created: int
    corroborated: int
    unchanged: int
    by_status: dict[str, int] = Field(default_factory=dict)


class EventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    brand: str = Field(min_length=1)
    occurred_on: str = Field(min_length=8)
    era: str | None = None
    source: EventSource = EventSource.MANUAL
    source_ref: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class EventDetailResponse(BaseModel):
    """FR-5/FR-6: the event plus its resolved scopes, priors and retirement age."""

    event: FashionEvent
    targets: list[dict[str, str]]
    priors: list[dict[str, Any]]
    retirement_age_weeks: float
    source_refs: list[str]
    corroboration: int


class BrandResponse(BaseModel):
    brands: list[Any]


class GarmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    era: str = Field(min_length=1)
    category: Category
    condition: ConditionGrade
    status: GarmentStatus = GarmentStatus.OWNED
    price: float = Field(gt=0, description="acquisition price, or reference price when watching")
    date: str = Field(min_length=8, description="acquisition date, or reference date")
    size: str | None = None
    notes: str = ""


class GarmentPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    condition: ConditionGrade | None = None
    size: str | None = None
    notes: str | None = None
    status: GarmentStatus | None = None
    disposed_price: float | None = None
    disposed_on: str | None = None


class ValuationResponse(BaseModel):
    """FR-7: the value and, always, the method that produced it."""

    garment_id: str
    as_of_week: str
    valuation_method: ValuationMethod
    fair_value: float | None = None
    reason: ValuationReason | None = None
    stratum_id: str | None = None
    level_usd: float | None = None
    anchor_price: float | None = None
    unrealized_gain: float | None = None


class PortfolioValuationResponse(BaseModel):
    as_of_week: str
    total_fair_value: float
    total_anchor_price: float
    unavailable: int
    garments: list[ValuationResponse]


class AdviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str | None = Field(
        default=None, description="ISO date; defaults to the latest index week"
    )


class AdviceBatchResponse(BaseModel):
    as_of_week: str
    counts: dict[AdviceAction, int]
    advice: list[Advice]


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str | None = None
    end: str | None = None
    placebo_seed: int | None = None
    reference: str = Field(default="recovered", pattern="^(recovered|truth)$")
    scenario: str = ""


class BacktestRunResponse(BaseModel):
    id: str
    params: dict[str, Any]
    as_of: str
    aggregates: dict[str, Any]
    n_results: int


class GarmentListResponse(BaseModel):
    garments: list[Garment]
