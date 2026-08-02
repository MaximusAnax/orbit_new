"""The `NewsFeed` capability: raw articles in, nothing else.

Offline implementations are the default and are what tests and evals exercise.
The live implementation lives in `newsfeed_rss.py` and is imported only when a
caller explicitly asks for it, so the offline path never pulls it in (FR-14
hermeticity).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..models import RawArticle


@runtime_checkable
class NewsFeed(Protocol):
    """Yields `RawArticle`s published in `[since, until]`."""

    def fetch(self, since: datetime, until: datetime) -> list[RawArticle]:
        """Return articles in deterministic (published_at, external_id) order."""
        ...


class NewsFeedError(RuntimeError):
    """A feed could not be read. Never degrade to silently stale analysis (US-8)."""


__all__ = ["NewsFeed", "NewsFeedError"]
