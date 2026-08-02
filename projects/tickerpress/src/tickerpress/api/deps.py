"""Service wiring for the API (SCOPE FR-14).

The app never constructs engine objects itself: it holds one
:class:`~tickerpress.services.TickerPressService` on ``app.state`` and hands it
to every route. Tests inject their own (offline adapters, ``FixedClock``); the
default builds a SQLite-backed one from the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request

from ..adapters.clock import SystemClock
from ..services import TickerPressService
from ..store.sqlite_store import SQLiteRepository

__all__ = ["DEFAULT_DB_PATH", "build_service", "get_service", "resolve_db_path"]

#: Where the archive lives unless ``TICKERPRESS_DB`` says otherwise.
DEFAULT_DB_PATH = "~/.tickerpress/tickerpress.db"


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """CLI/API argument, then ``TICKERPRESS_DB``, then the default location."""

    raw = str(db_path) if db_path is not None else os.environ.get("TICKERPRESS_DB")
    return Path(raw or DEFAULT_DB_PATH).expanduser()


def build_service(db_path: str | Path | None = None) -> TickerPressService:
    """Build the default, production service: SQLite archive + system clock."""

    repository = SQLiteRepository(resolve_db_path(db_path))
    repository.initialize()
    outbox = os.environ.get("TICKERPRESS_OUTBOX")
    from ..services import NotifierRegistry

    registry = NotifierRegistry(outbox_dir=outbox or Path.cwd() / "outbox")
    return TickerPressService(repository, clock=SystemClock(), notifiers=registry)


def get_service(request: Request) -> TickerPressService:
    """FastAPI dependency: the service this app was created with."""

    service = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - lifespan always sets it
        raise RuntimeError("no TickerPressService configured on the app")
    return service
