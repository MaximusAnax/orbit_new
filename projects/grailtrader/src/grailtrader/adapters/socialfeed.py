"""The ``SocialFeed`` capability (FR-5).

There is no live scraping (SCOPE non-goal 6): platform APIs are gated and
unstable, and scraping them is against their terms. The live path is manual
entry; the fixture path models what a future integration would emit.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import FashionEvent

__all__ = ["SocialFeed"]


@runtime_checkable
class SocialFeed(Protocol):
    """Yields typed social-sourced events (co-signs, runway reception)."""

    def fetch(self, since: str, until: str) -> list[FashionEvent]:
        """Return events with ``since <= occurred_on <= until``, deterministically ordered."""
        ...
