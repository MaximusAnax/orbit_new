"""HTTP error catalog for the TickerPress API (SCOPE FR-14).

Every failure the API can produce is one of the five entries below, and every
one of them is rendered with the same envelope::

    {"error": {"code": "not_found", "message": "...", "detail": {...}}}

| status | code                 | raised when                                        |
|--------|----------------------|----------------------------------------------------|
| 404    | ``not_found``        | unknown ticker, alias, feed, article, story, run    |
| 409    | ``conflict``         | duplicate ticker/alias/feed, or a feed that still   |
|        |                      | has archived articles being deleted                 |
| 422    | ``validation_error`` | a payload that fails schema or domain validation    |
| 503    | ``channel_unavailable`` | a live channel requested without its credentials |
| 500    | ``internal_error``   | anything unforeseen (never leaks a traceback)       |

The layer is deliberately thin: it maps exceptions the service already raises
onto status codes, and never decides anything about the domain.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..engine.watchlist import DuplicateAliasError
from ..services import UnknownChannelError, UnknownCompanyError

__all__ = [
    "ApiError",
    "ChannelUnavailable",
    "Conflict",
    "NotFound",
    "ValidationFailed",
    "error_body",
    "install_error_handlers",
]


def error_body(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    """The one error envelope every failure is rendered with."""

    return {"error": {"code": code, "message": message, "detail": detail}}


class ApiError(Exception):
    """Base class for the catalog above."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=error_body(self.code, self.message, self.detail),
        )


class NotFound(ApiError):
    status_code = 404
    code = "not_found"


class Conflict(ApiError):
    status_code = 409
    code = "conflict"


class ValidationFailed(ApiError):
    status_code = 422
    code = "validation_error"


class ChannelUnavailable(ApiError):
    status_code = 503
    code = "channel_unavailable"


def install_error_handlers(app: FastAPI) -> None:
    """Register one handler per catalog entry on ``app``."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(UnknownCompanyError)
    async def _unknown_company(_: Request, exc: UnknownCompanyError) -> JSONResponse:
        ticker = exc.args[0] if exc.args else "?"
        return NotFound(f"unknown company {ticker}", detail={"ticker": ticker}).response()

    @app.exception_handler(KeyError)
    async def _unknown_key(_: Request, exc: KeyError) -> JSONResponse:
        subject = exc.args[0] if exc.args else "?"
        return NotFound(f"not found: {subject}", detail={"key": str(subject)}).response()

    @app.exception_handler(UnknownChannelError)
    async def _unknown_channel(_: Request, exc: UnknownChannelError) -> JSONResponse:
        return ChannelUnavailable(str(exc)).response()

    @app.exception_handler(DuplicateAliasError)
    async def _duplicate_alias(_: Request, exc: DuplicateAliasError) -> JSONResponse:
        return Conflict(str(exc)).response()

    @app.exception_handler(sqlite3.IntegrityError)
    async def _integrity(_: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
        return Conflict(f"constraint violation: {exc}").response()

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "invalid request", exc.errors()),
        )

    @app.exception_handler(ValidationError)
    async def _model_validation(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "validation_error",
                "invalid payload",
                [{"loc": list(err["loc"]), "msg": err["msg"]} for err in exc.errors()],
            ),
        )

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return ValidationFailed(str(exc)).response()
