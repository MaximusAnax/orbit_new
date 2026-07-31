"""Offline `NewsFeed`: a committed JSONL corpus. The default everywhere."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..engine.normalize import parse_iso
from ..models import RawArticle
from .newsfeed import NewsFeedError

_REQUIRED = ("external_id", "source_domain", "published_at", "title", "body")


class FixtureNewsFeed:
    """Reads `RawArticle`s from a JSONL file; order is (published_at, external_id).

    Extra keys used by the eval fixtures (`family`, cluster annotations, ...) are
    ignored, so one corpus file can carry both the articles and their labels.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def fetch(self, since: datetime, until: datetime) -> list[RawArticle]:
        articles = [a for a in self.load() if since <= parse_iso(a.published_at) <= until]
        return sorted(articles, key=lambda a: (a.published_at, a.external_id))

    def load(self) -> list[RawArticle]:
        """Every article in the corpus, in file order."""
        if not self.path.exists():
            raise NewsFeedError(f"fixture corpus not found: {self.path}")
        articles: list[RawArticle] = []
        with self.path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise NewsFeedError(f"{self.path}:{lineno} is not valid JSON: {exc}") from exc
                missing = [field for field in _REQUIRED if field not in row]
                if missing:
                    raise NewsFeedError(
                        f"{self.path}:{lineno} is missing required field(s) {missing}"
                    )
                articles.append(
                    RawArticle(
                        external_id=row["external_id"],
                        url=row.get("url"),
                        source_domain=row["source_domain"],
                        published_at=row["published_at"],
                        published_at_estimated=bool(row.get("published_at_estimated", False)),
                        fetched_at=row.get("fetched_at", row["published_at"]),
                        title=row["title"],
                        body=row["body"],
                    )
                )
        return articles


class StaticNewsFeed:
    """In-memory `NewsFeed` over a list of articles -- for tests and API payloads."""

    def __init__(self, articles: list[RawArticle]) -> None:
        self._articles = list(articles)

    def fetch(self, since: datetime, until: datetime) -> list[RawArticle]:
        selected = [a for a in self._articles if since <= parse_iso(a.published_at) <= until]
        return sorted(selected, key=lambda a: (a.published_at, a.external_id))


__all__ = ["FixtureNewsFeed", "StaticNewsFeed"]
