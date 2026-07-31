"""Live `NewsFeed`: RSS/Atom polling.

Activates only when `NEWSALPHA_FEEDS` is set (comma-separated feed URLs).  The
optional `feedparser` dependency is imported lazily inside `fetch`, so importing
this module never pulls it in and the offline path never imports this module at
all (FR-14 hermeticity, asserted by `test_hermetic_no_live_adapters`).

Environment:
    NEWSALPHA_FEEDS      comma-separated RSS/Atom URLs (required to activate)
    NEWSALPHA_USER_AGENT optional User-Agent override

Feeds lacking a published timestamp fall back to fetch time and are flagged
`published_at_estimated`; signals built from them are excluded from backtests
with `estimated_publish_time` (SCOPE D-19).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import urlparse

from ..engine.normalize import iso_utc
from ..models import RawArticle
from .newsfeed import NewsFeedError

FEEDS_ENV = "NEWSALPHA_FEEDS"
USER_AGENT_ENV = "NEWSALPHA_USER_AGENT"
DEFAULT_USER_AGENT = "newsalpha/0.1 (+https://example.invalid/newsalpha)"


def feeds_from_env(environ: dict[str, str] | None = None) -> list[str]:
    env = environ if environ is not None else dict(os.environ)
    raw = env.get(FEEDS_ENV, "").strip()
    return [url.strip() for url in raw.split(",") if url.strip()]


def is_configured(environ: dict[str, str] | None = None) -> bool:
    """True when `NEWSALPHA_FEEDS` names at least one feed."""
    return bool(feeds_from_env(environ))


class RSSNewsFeed:
    """`feedparser` over the URLs in `NEWSALPHA_FEEDS`.

    Raises `NewsFeedError` when the optional dependency is missing or a feed cannot
    be read -- a live fetch failure is a clear error, never silently stale analysis.
    """

    def __init__(
        self,
        urls: list[str] | None = None,
        *,
        environ: dict[str, str] | None = None,
        fetched_at: datetime | None = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.urls = list(urls) if urls is not None else feeds_from_env(env)
        if not self.urls:
            raise NewsFeedError(
                f"{FEEDS_ENV} is not set: the live RSS feed only activates when it names "
                "at least one feed URL"
            )
        self.user_agent = env.get(USER_AGENT_ENV, DEFAULT_USER_AGENT)
        self._fetched_at = fetched_at

    def fetch(self, since: datetime, until: datetime) -> list[RawArticle]:
        parser = self._import_feedparser()
        fetched_at = self._fetched_at or datetime.now(tz=UTC)
        articles: list[RawArticle] = []
        for url in self.urls:
            parsed = parser.parse(url, agent=self.user_agent)
            if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", []):
                raise NewsFeedError(f"could not read feed {url}: {parsed.get('bozo_exception')}")
            for entry in parsed.entries:
                article = self._to_article(entry, url, fetched_at)
                published = datetime.fromisoformat(article.published_at.replace("Z", "+00:00"))
                if since <= published <= until:
                    articles.append(article)
        return sorted(articles, key=lambda a: (a.published_at, a.external_id))

    @staticmethod
    def _import_feedparser():  # pragma: no cover - exercised only with the extra installed
        try:
            import feedparser
        except ModuleNotFoundError as exc:
            raise NewsFeedError(
                "the live RSS feed needs the optional 'live' extra: "
                "uv pip install 'newsalpha[live]'"
            ) from exc
        return feedparser

    def _to_article(self, entry, feed_url: str, fetched_at: datetime) -> RawArticle:
        link = getattr(entry, "link", None)
        external_id = getattr(entry, "id", None) or link or getattr(entry, "title", "")
        if not external_id:
            raise NewsFeedError(f"feed {feed_url} produced an entry with no id, link or title")
        published_struct = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        estimated = published_struct is None
        if published_struct is None:
            published = fetched_at
        else:
            published = datetime(*published_struct[:6], tzinfo=UTC)
        body = ""
        content = getattr(entry, "content", None)
        if content:
            body = content[0].get("value", "")
        if not body:
            body = getattr(entry, "summary", "") or ""
        return RawArticle(
            external_id=str(external_id),
            url=link,
            source_domain=(urlparse(link or feed_url).hostname or feed_url).lower(),
            published_at=iso_utc(published),
            published_at_estimated=estimated,
            fetched_at=iso_utc(fetched_at),
            title=getattr(entry, "title", "") or "",
            body=body,
        )


__all__ = [
    "DEFAULT_USER_AGENT",
    "FEEDS_ENV",
    "RSSNewsFeed",
    "feeds_from_env",
    "is_configured",
]
