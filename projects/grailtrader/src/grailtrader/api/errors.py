"""The API error catalog (FR-12).

Every failure the service can raise maps to exactly one stable ``code`` and one
HTTP status, so a client can branch on the code instead of parsing prose. The
mapping lives here and nowhere else; route handlers never build error responses.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..engine.conditions import UnmappedConditionLabelError
from ..engine.frame import FrameCheckError
from ..engine.strata import UnknownReferenceError
from ..engine.validate import DatasetValidationError
from ..service import NotFoundError, PreconditionError
from ..store.base import RepositoryError

__all__ = ["ErrorBody", "ErrorResponse", "install_error_handlers"]

#: 422. Spelled out because Starlette renamed the constant and deprecated the old name.
_UNPROCESSABLE = 422


class ErrorBody(BaseModel):
    code: str = Field(description="stable machine-readable error code")
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """The single error shape every non-2xx response uses."""

    error: ErrorBody


def _payload(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "detail": detail}}


def install_error_handlers(app: FastAPI) -> None:
    """Register the catalog on ``app``."""

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_payload("not_found", str(exc), kind=exc.kind, id=exc.identifier),
        )

    @app.exception_handler(UnknownReferenceError)
    async def _unknown_reference(_: Request, exc: UnknownReferenceError) -> JSONResponse:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_payload(
                "unknown_reference",
                str(exc),
                kind=exc.kind,
                value=exc.value,
                suggestion=exc.suggestion,
            ),
        )

    @app.exception_handler(UnmappedConditionLabelError)
    async def _unmapped_condition(_: Request, exc: UnmappedConditionLabelError) -> JSONResponse:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_payload("unmapped_condition_label", str(exc), label=exc.label),
        )

    @app.exception_handler(PreconditionError)
    async def _precondition(_: Request, exc: PreconditionError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload("precondition_failed", str(exc)),
        )

    @app.exception_handler(RepositoryError)
    async def _repository(_: Request, exc: RepositoryError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=_payload("conflict", str(exc))
        )

    @app.exception_handler(FrameCheckError)
    async def _frame(_: Request, exc: FrameCheckError) -> JSONResponse:
        # FR-9: rendering failed its own safeguard, so nothing was persisted.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("frame_check_failed", str(exc), violations=list(exc.violations)),
        )

    @app.exception_handler(DatasetValidationError)
    async def _dataset(_: Request, exc: DatasetValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("dataset_invalid", str(exc), record=exc.record),
        )

    @app.exception_handler(FileNotFoundError)
    async def _missing_file(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_payload("feed_unreadable", f"cannot read feed: {exc}"),
        )

    @app.exception_handler(RuntimeError)
    async def _runtime(_: Request, exc: RuntimeError) -> JSONResponse:
        # Live adapters raise RuntimeError when their credentials/deps are absent.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_payload("feed_unavailable", str(exc)),
        )

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_payload("invalid_request", str(exc)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_payload("invalid_request", "request validation failed", errors=exc.errors()),
        )
