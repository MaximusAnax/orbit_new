"""FeedSource port (SCOPE FR-2, US-8).

Only the *fetch* differs between offline and live: parsing is shared pure engine
code (:mod:`tickerpress.engine.feedparse`), so the fixture path exercises the
same parser production uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..engine.feedparse import FeedItem
from ..engine.models import Feed, FetchStatus

__all__ = ["FeedItem", "FeedSource", "FetchResult"]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of one fetch attempt.

    ``etag``/``last_modified`` are echoed back so the caller can persist the
    conditional-GET state (RFC 9110) for the next poll.
    """

    status: FetchStatus
    raw_bytes: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is FetchStatus.OK

    @classmethod
    def failure(cls, error: str) -> FetchResult:
        return cls(status=FetchStatus.ERROR, error=error)

    @classmethod
    def unchanged(cls, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        return cls(status=FetchStatus.NOT_MODIFIED, etag=etag, last_modified=last_modified)


@runtime_checkable
class FeedSource(Protocol):
    """Fetches raw feed bytes for one registered feed.

    ``now`` is passed in rather than read from a clock so that polite-polling
    decisions stay deterministic under :class:`~tickerpress.adapters.clock.FixedClock`.
    """

    def fetch(self, feed: Feed, now: datetime) -> FetchResult: ...
