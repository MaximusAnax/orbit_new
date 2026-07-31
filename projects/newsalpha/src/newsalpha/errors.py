"""The error catalog shared by both edges (FR-12/FR-13).

Every failure the API and CLI can surface carries a stable `code`.  The HTTP
status map lives in `api/app.py` and the exit-code map in `cli/main.py`, so a new
rule means one row here and one row there -- never a new branch in a handler.

Errors raised deeper in the stack (`DatasetError`, `RepositoryError`,
`NewsFeedError`, `MarketDataError`, `FrameCheckError`) are translated into these
at the edge by `from_exception`, so a caller never has to know which layer failed.
"""

from __future__ import annotations

from typing import Any


class NewsAlphaError(RuntimeError):
    """Base class for every edge-visible failure."""

    code = "internal_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = {k: v for k, v in details.items() if v is not None}

    def payload(self) -> dict[str, Any]:
        """The JSON body both edges render."""
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class NotFoundError(NewsAlphaError):
    """A requested entity does not exist. Subclasses name which one."""

    code = "not_found"


class UnknownArticleError(NotFoundError):
    code = "unknown_article"


class UnknownEventError(NotFoundError):
    code = "unknown_event"


class UnknownSignalError(NotFoundError):
    code = "unknown_signal"


class UnknownBriefError(NotFoundError):
    code = "unknown_brief"


class UnknownBacktestError(NotFoundError):
    code = "unknown_backtest"


class UnknownAssetError(NotFoundError):
    """An asset id outside the committed gazetteer (FR-11 suggests the nearest)."""

    code = "unknown_asset"


class InvalidRequestError(NewsAlphaError):
    """A syntactically valid request the domain rejects (bad date, bad enum value)."""

    code = "invalid_request"


class DatasetInvalidError(NewsAlphaError):
    """A committed dataset failed FR-3 validation; `init` aborts naming the record."""

    code = "dataset_invalid"


class FeedUnavailableError(NewsAlphaError):
    """A news feed could not be read -- never degrade to silently stale analysis (US-8)."""

    code = "feed_unavailable"


class MarketDataUnavailableError(NewsAlphaError):
    """A price series could not be read."""

    code = "market_data_unavailable"


class FrameCheckFailedError(NewsAlphaError):
    """A brief failed the FR-7 frame check, so the ingest run produced nothing."""

    code = "frame_check_failed"


class ConflictError(NewsAlphaError):
    """A write violated an append-only or uniqueness invariant."""

    code = "conflict"


def from_exception(exc: Exception) -> NewsAlphaError:
    """Translate a lower-layer exception into the catalog. Import-light on purpose."""
    if isinstance(exc, NewsAlphaError):
        return exc

    from .adapters.marketdata import MarketDataError
    from .adapters.newsfeed import NewsFeedError
    from .datasets import DatasetError
    from .engine.frame import FrameCheckError
    from .store.base import RepositoryError

    if isinstance(exc, FrameCheckError):
        return FrameCheckFailedError(str(exc), violations=list(exc.violations))
    if isinstance(exc, DatasetError):
        return DatasetInvalidError(str(exc))
    if isinstance(exc, NewsFeedError):
        return FeedUnavailableError(str(exc))
    if isinstance(exc, MarketDataError):
        return MarketDataUnavailableError(str(exc))
    if isinstance(exc, RepositoryError):
        return ConflictError(str(exc))
    if isinstance(exc, ValueError):
        return InvalidRequestError(str(exc))
    raise exc


__all__ = [
    "ConflictError",
    "DatasetInvalidError",
    "FeedUnavailableError",
    "FrameCheckFailedError",
    "InvalidRequestError",
    "MarketDataUnavailableError",
    "NewsAlphaError",
    "NotFoundError",
    "UnknownArticleError",
    "UnknownAssetError",
    "UnknownBacktestError",
    "UnknownBriefError",
    "UnknownEventError",
    "UnknownSignalError",
    "from_exception",
]
