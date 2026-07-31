"""FastAPI surface (FR-14).

``create_app()`` builds the application; ``app`` is the ASGI entry point uvicorn
loads (``uvicorn pointsmax.api:app``), constructed lazily so importing this
package in tests does not touch the filesystem.
"""

from __future__ import annotations

from typing import Any

from .app import create_app, default_db_path, get_service, utc_now
from .errors import ERROR_STATUS, status_for

__all__ = [
    "ERROR_STATUS",
    "create_app",
    "default_db_path",
    "get_service",
    "status_for",
    "utc_now",
]


def __getattr__(name: str) -> Any:
    """Lazily construct the default ASGI app on first attribute access."""
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
