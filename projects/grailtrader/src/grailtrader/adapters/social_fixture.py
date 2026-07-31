"""Offline ``SocialFeed``: committed JSONL of typed social events."""

from __future__ import annotations

import os
from pathlib import Path

from ..models import EventSource, FashionEvent
from .news_fixture import read_event_jsonl

__all__ = ["FixtureSocialFeed"]


class FixtureSocialFeed:
    """Reads committed social-sourced events (co-signs, runway reception)."""

    default_source = EventSource.SOCIAL

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def fetch(self, since: str, until: str) -> list[FashionEvent]:
        return [
            event
            for event in read_event_jsonl(self.path, default_source=self.default_source)
            if since <= event.occurred_on <= until
        ]
