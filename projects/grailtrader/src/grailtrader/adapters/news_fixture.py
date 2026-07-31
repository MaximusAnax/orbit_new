"""Offline ``NewsFeed``/``SocialFeed`` reader: committed JSONL of typed events.

Each line carries ``event_type``, ``brand_id``, optional ``era_id``,
``attributes``, ``occurred_on``, ``source``, ``source_refs``, optional ``status``
(defaulting to ``confirmed``) and optional ``notes``. Ids are re-derived from the
content, so a fixture file can never disagree with FR-5's identity rule.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..engine.events import make_event
from ..models import EventSource, EventStatus, EventType, FashionEvent

__all__ = ["FixtureNewsFeed", "read_event_jsonl"]


def read_event_jsonl(
    path: str | os.PathLike[str], *, default_source: EventSource
) -> list[FashionEvent]:
    """Parse a committed JSONL file of typed events."""
    file_path = Path(path)
    events: list[FashionEvent] = []
    with file_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file_path}:{number}: invalid JSON ({exc})") from None
            source = EventSource(payload.get("source", default_source.value))
            refs = payload.get("source_refs") or [
                payload.get("source_ref") or f"{source.value}:{file_path.stem}-{number}"
            ]
            try:
                events.append(
                    make_event(
                        event_type=EventType(payload["event_type"]),
                        brand_id=payload["brand_id"],
                        occurred_on=payload["occurred_on"],
                        source=source,
                        source_refs=refs,
                        status=EventStatus(payload.get("status", EventStatus.CONFIRMED.value)),
                        era_id=payload.get("era_id"),
                        attributes=payload.get("attributes") or {},
                        notes=payload.get("notes", ""),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{file_path}:{number}: {exc}") from None
    events.sort(key=lambda event: (event.occurred_on, event.id))
    return events


class FixtureNewsFeed:
    """Reads committed, already-typed news events (the default for tests and evals)."""

    default_source = EventSource.NEWS

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def fetch(self, since: str, until: str) -> list[FashionEvent]:
        return [
            event
            for event in read_event_jsonl(self.path, default_source=self.default_source)
            if since <= event.occurred_on <= until
        ]
