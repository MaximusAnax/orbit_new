"""The API error catalog (FR-14).

Every domain failure in the engine, the store and the service layer already
carries a stable ``code``; this module is the single place that maps those codes
to HTTP status codes and renders the one error envelope
(:class:`~pointsmax.api.schemas.ErrorOut`) the whole surface uses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..engine.execution import ExecutionError
from ..engine.world import WorldValidationError
from ..service import ServiceError
from ..store import StoreError

#: The status codes this surface uses, spelled out so the catalog below reads as
#: one table (and so a Starlette rename cannot silently shift a status).
BAD_REQUEST = 400
NOT_FOUND = 404
CONFLICT = 409
PRECONDITION_REQUIRED = 428
UNPROCESSABLE = 422
SERVER_ERROR = 500

#: code -> HTTP status.  Codes come from the exception classes themselves, so a
#: new domain error is a one-line addition here and nowhere else.
ERROR_STATUS: dict[str, int] = {
    # service layer
    "unknown_entity": UNPROCESSABLE,
    "invalid_goal": UNPROCESSABLE,
    "goal_parse_failed": UNPROCESSABLE,
    "search_budget_exceeded": UNPROCESSABLE,
    "duplicate_card": CONFLICT,
    "world_invalid": SERVER_ERROR,
    "service_error": BAD_REQUEST,
    # store
    "not_found": NOT_FOUND,
    "negative_balance": CONFLICT,
    "ledger_chain_broken": CONFLICT,
    "invalid_transition": CONFLICT,
    "store_error": BAD_REQUEST,
    # execution (FR-11)
    "world_pin_mismatch": CONFLICT,
    "step_out_of_order": CONFLICT,
    "step_already_executed": CONFLICT,
    "confirmation_required": PRECONDITION_REQUIRED,
    "insufficient_balance": CONFLICT,
    "execution_error": BAD_REQUEST,
    # world load (FR-1)
    "world_validation_failed": SERVER_ERROR,
}


def status_for(code: str) -> int:
    """HTTP status for a domain error code (400 for anything unmapped)."""
    return ERROR_STATUS.get(code, BAD_REQUEST)


def error_response(
    code: str, message: str, detail: dict[str, object] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_for(code),
        content={"code": code, "message": message, "detail": detail or {}},
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register one handler per domain exception family."""

    async def _coded(_request: Request, exc: Exception) -> JSONResponse:
        payload = exc.as_dict()  # type: ignore[attr-defined]
        code = str(payload.pop("code"))
        message = str(payload.pop("message"))
        return error_response(code, message, payload)

    async def _world(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, WorldValidationError)
        return error_response(
            "world_validation_failed",
            "the rewards world failed FR-1 validation",
            {"issues": [{"code": i.code, "message": i.message} for i in exc.issues]},
        )

    app.add_exception_handler(ServiceError, _coded)
    app.add_exception_handler(StoreError, _coded)
    app.add_exception_handler(ExecutionError, _coded)
    app.add_exception_handler(WorldValidationError, _world)
