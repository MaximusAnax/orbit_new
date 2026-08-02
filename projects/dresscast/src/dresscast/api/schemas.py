"""Request/response schemas for the REST surface (SCOPE.md FR-17).

These are transport shapes only.  Domain entities (``Garment``,
``Recommendation``, ``DayBrief``, …) are already Pydantic v2 models in
``engine.models`` and are returned as-is; what lives here are the request
bodies and the error envelope.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dresscast.engine.models import DEFAULT_K, MET_DEFAULT


class ErrorDetail(BaseModel):
    """The structured detail every 4xx carries (SCOPE.md FR-17's catalog)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


class ColorIn(BaseModel):
    """A colour may be a bare name (``"navy"``) or a full mapping."""

    model_config = ConfigDict(extra="forbid")

    name: str
    hue: float | None = Field(default=None, ge=0.0, le=360.0)
    neutral: bool | None = None
    role: Literal["main", "accent"] | None = None


class GarmentCreate(BaseModel):
    """FR-1: everything ``POST /garments`` accepts; presets fill the rest."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    category: str
    colors: list[str | ColorIn] = Field(min_length=1, max_length=3)
    occasions: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)
    clo: float | None = Field(default=None, ge=0.0, le=1.5)
    warmth: int | None = Field(default=None, ge=0, le=5)
    layer_role: str | None = None
    accessory_class: str | None = None
    formality: int | None = Field(default=None, ge=1, le=5)
    waterproofness: int = Field(default=0, ge=0, le=3)
    windproofness: int = Field(default=0, ge=0, le=2)
    wears_before_laundry: int | None = Field(default=None, ge=1)
    notes: str | None = None


class GarmentPatch(BaseModel):
    """FR-1 edits plus FR-3 status transitions; every field optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    colors: list[str | ColorIn] | None = None
    occasions: list[str] | None = None
    style_tags: list[str] | None = None
    clo: float | None = Field(default=None, ge=0.0, le=1.5)
    warmth: int | None = Field(default=None, ge=0, le=5)
    layer_role: str | None = None
    formality: int | None = Field(default=None, ge=1, le=5)
    waterproofness: int | None = Field(default=None, ge=0, le=3)
    windproofness: int | None = Field(default=None, ge=0, le=2)
    wears_before_laundry: int | None = Field(default=None, ge=1)
    status: Literal["clean", "dirty", "in_laundry", "retired"] | None = None
    notes: str | None = None


class PhotoAttach(BaseModel):
    """FR-2 photo attach.

    The photo is an ordinary image file on the same machine (SCOPE.md
    §Target user), so the body names a path rather than streaming bytes; see
    REVIEW.md §Build-stage notes.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class SuggestionAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[str] = Field(min_length=1)


class RecommendationRequest(BaseModel):
    """FR-8's request body.  Objective weights are engine constants (D13)."""

    model_config = ConfigDict(extra="forbid")

    date: str
    occasion: str | None = None
    wear_window: tuple[int, int] | None = None
    commute_hours: list[int] | None = None
    met: float | None = Field(default=MET_DEFAULT, gt=0.0, le=5.0)
    k: int | None = Field(default=DEFAULT_K, ge=1, le=10)
    seed: int = 0


class WearFromRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(default=1, ge=1)
    date: str | None = None


class WearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    garment_ids: list[str] = Field(min_length=1)
    date: str | None = None


class LaundryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    garment_ids: list[str] = Field(default_factory=list)
    all_dirty: bool = False
    note: str | None = None


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    version: str
    engine_version: str
    schema_version: int
