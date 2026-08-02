"""The ``NewsFeed`` capability (FR-5).

Feeds emit *typed* ``FashionEvent``s — there is no NLP extraction in this pass
(SCOPE non-goal 5). Fixture and manual events arrive ``confirmed``; live RSS
candidates arrive ``pending`` and require ``events review --confirm`` before they
influence anything.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import FashionEvent

__all__ = ["NewsFeed"]


@runtime_checkable
class NewsFeed(Protocol):
    """Yields typed fashion events occurring in ``[since, until]``."""

    def fetch(self, since: str, until: str) -> list[FashionEvent]:
        """Return events with ``since <= occurred_on <= until``, deterministically ordered."""
        ...
