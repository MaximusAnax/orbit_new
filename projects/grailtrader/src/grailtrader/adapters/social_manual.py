"""Live ``SocialFeed``: manual entry (SCOPE non-goal 6 — no scraping, ever).

``events add --source social`` writes through this seam. Entries are ``confirmed``
on arrival, exactly like manual news entries: a human is the extraction QA.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..engine.events import make_event
from ..engine.strata import Gazetteer
from ..models import EventSource, EventStatus, EventType, FashionEvent

__all__ = ["ManualSocialEntry"]


class ManualSocialEntry:
    """An in-process queue of manually entered social events."""

    default_source = EventSource.SOCIAL

    def __init__(self, gazetteer: Gazetteer | None = None) -> None:
        self._gazetteer = gazetteer
        self._events: list[FashionEvent] = []

    def add(
        self,
        *,
        event_type: EventType,
        brand_ref: str,
        occurred_on: str,
        attributes: Mapping[str, Any] | None = None,
        era_id: str | None = None,
        slug: str | None = None,
        notes: str = "",
        source_refs: Sequence[str] | None = None,
    ) -> FashionEvent:
        """Record one manually observed social event and return it."""
        brand_id = (
            self._gazetteer.resolve_brand(brand_ref) if self._gazetteer is not None else brand_ref
        )
        refs = tuple(source_refs or (f"social:{slug or brand_id}-{occurred_on}",))
        event = make_event(
            event_type=event_type,
            brand_id=brand_id,
            occurred_on=occurred_on,
            source=EventSource.SOCIAL,
            source_refs=refs,
            status=EventStatus.CONFIRMED,
            era_id=era_id,
            attributes=attributes,
            notes=notes,
        )
        self._events.append(event)
        return event

    def fetch(self, since: str, until: str) -> list[FashionEvent]:
        events = [e for e in self._events if since <= e.occurred_on <= until]
        events.sort(key=lambda event: (event.occurred_on, event.id))
        return events
