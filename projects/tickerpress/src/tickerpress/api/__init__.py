"""FastAPI surface (SCOPE FR-14).

``uvicorn tickerpress.api:app`` serves the default, SQLite-backed application;
tests and the eval suite build their own with an injected service.
"""

from __future__ import annotations

from .app import create_app
from .errors import ApiError, ChannelUnavailable, Conflict, NotFound, ValidationFailed

__all__ = [
    "ApiError",
    "ChannelUnavailable",
    "Conflict",
    "NotFound",
    "ValidationFailed",
    "app",
    "create_app",
]

#: Default application. Building it is cheap — the repository is opened by the
#: lifespan handler, not at import time.
app = create_app()
