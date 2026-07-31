"""The ``ListingsFeed`` capability (FR-2).

A listings feed yields raw marketplace rows; normalisation, gazetteer resolution
and idempotency all happen in :mod:`grailtrader.engine.ingest`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import RawListing

__all__ = ["ListingsFeed"]


@runtime_checkable
class ListingsFeed(Protocol):
    """Yields raw listings in a deterministic order."""

    def fetch(self) -> list[RawListing]:
        """Return every listing the feed can see, deterministically ordered."""
        ...
