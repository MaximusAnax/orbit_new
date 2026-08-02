"""Request/response schemas for the datasweep API (SCOPE.md FR-14).

Responses reuse the domain models from ``engine.models`` wherever the wire
shape *is* the domain shape — duplicating them would only create two places for
a field to drift.  Only request bodies and the few API-specific envelopes live
here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..engine.models import DEFAULT_INCLUDE


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    version: str


class FolderCreate(BaseModel):
    """``POST /folders`` (FR-1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    recursive: bool = True
    include: list[str] = Field(default_factory=lambda: list(DEFAULT_INCLUDE))
    policy_path: str | None = None
    output_dir: str | None = None


class ScanRequest(BaseModel):
    """``POST /scan`` (FR-1/FR-2/FR-13)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    force: bool = False


class CleanRequest(BaseModel):
    """``POST /clean`` (US-5, FR-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    policy_path: str | None = None
    out: str | None = None
    force: bool = False
    sheet: int | str | None = None


class DecisionRequest(BaseModel):
    """``POST /runs/{run_id}/decisions`` (FR-11)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accept: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    """The structured 4xx body every service error maps to (FR-14)."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
