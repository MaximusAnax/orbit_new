"""Request/response schemas for the FR-15 API.

Domain models (`almanac.models`) are reused for responses wherever the wire
shape *is* the domain shape; the models here are the request bodies and the
few envelopes that carry extra context (warnings, error catalog entries).
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from almanac.models import (
    Entry,
    EntryKind,
    Grade,
    ImportCandidate,
    ThemeSuggestion,
)


class ErrorBody(BaseModel):
    """The single error shape every non-2xx response carries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error: ErrorBody


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = "ok"
    version: str
    scheduler_version: str
    today: dt.date
    seed: int
    batch_k: int


class CreateEntryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    kind: EntryKind = EntryKind.QUOTE
    author: str | None = None
    source: str | None = None
    url: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list, max_length=3)
    captured_on: dt.date | None = None
    accept_suggestions: bool = False


class PatchEntryRequest(BaseModel):
    """Every field optional; omitted fields are left alone (FR-4)."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    author: str | None = None
    source: str | None = None
    url: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    themes: list[str] | None = Field(default=None, max_length=3)


class PinResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: Entry
    warning: str | None = None


class SuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    note: str | None = None


class SuggestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suggestions: list[ThemeSuggestion]


class ImportRequest(BaseModel):
    """FR-5: ``{format: json|csv|starter, payload?}``."""

    model_config = ConfigDict(extra="forbid")

    format: str = Field(pattern="^(json|csv|starter|rows)$")
    payload: str | None = None
    rows: list[ImportCandidate] | None = None


class TodayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None


class DrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None
    theme: str | None = None
    collection_id: str | None = None


class ReflectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: Grade
    text: str | None = Field(default=None, max_length=4000)


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str | None = None


class CollectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None


class CollectionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    description: str | None
    created_at: dt.datetime
    entry_ids: list[str] = Field(default_factory=list)


class EntryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int
    entries: list[Entry]


class TemplateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    theme_id: str
    kind: str
    template: str
