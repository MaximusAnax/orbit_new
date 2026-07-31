"""Structured errors shared by every layer.

Each error carries the ``code`` that SCOPE.md FR-17 requires the API to
surface as a structured detail code, so the API/CLI edge can map an exception
to a response without re-deriving the classification.
"""

from __future__ import annotations

from typing import Any


class DresscastError(Exception):
    """Base class for every application error with a structured detail code."""

    code = "internal_error"

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class InvalidParams(DresscastError):
    """A request or record violates a documented validation rule (FR-1/FR-2)."""

    code = "invalid_params"


class UnknownGarment(DresscastError):
    code = "unknown_garment"


class InvalidTransition(DresscastError):
    """A garment status change outside DATA_MODEL.md §2.1's state machine."""

    code = "invalid_transition"


class NoExtractorConfigured(DresscastError):
    code = "no_extractor_configured"


class WardrobeTooLarge(DresscastError):
    code = "wardrobe_too_large"


class InfeasibleWardrobe(DresscastError):
    """FR-14: the relaxation ladder was exhausted.

    ``detail`` carries ``missing`` (the named missing capability) and
    ``brief`` (the FR-16 day brief payload) so the caller can tell the user
    what the day demands even though the closet cannot serve it.
    """

    code = "infeasible_wardrobe"


class ForecastUnavailable(DresscastError):
    code = "forecast_unavailable"


class InvalidSchemaVersion(DresscastError):
    code = "invalid_schema_version"
