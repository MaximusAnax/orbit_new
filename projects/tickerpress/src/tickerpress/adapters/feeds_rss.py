"""Live FeedSource: polite HTTP polling (SCOPE US-8, D10).

Env-gated and dependency-gated. It activates only when
``TICKERPRESS_ALLOW_NETWORK=1`` and the feed URL is http(s), and it needs the
optional ``live`` extra (httpx). Nothing about tests or evals changes when it is
absent: the offline twin is the default everywhere.

Politeness follows RFC 9110 conditional requests — ``If-None-Match`` /
``If-Modified-Since`` from the feed's stored ``etag``/``last_modified``, with
304 treated as "nothing new" without parsing — plus a 15-minute minimum poll
spacing, long-standing RSS-community etiquette toward small publishers.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from ..engine.models import Feed, FetchStatus, ensure_utc
from .feeds import FetchResult

__all__ = ["ALLOW_NETWORK_ENV", "MIN_POLL_INTERVAL", "LiveRssFeedSource", "MissingDependencyError"]

ALLOW_NETWORK_ENV = "TICKERPRESS_ALLOW_NETWORK"
MIN_POLL_INTERVAL = timedelta(minutes=15)
_USER_AGENT = "tickerpress/0.1 (+https://example.invalid/tickerpress)"


class MissingDependencyError(RuntimeError):
    """The live adapter was used without its optional dependency installed."""


class LiveRssFeedSource:
    """Fetch a real feed over HTTP.

    Errors are returned as ``FetchResult(status=error)`` rather than raised, so
    one unreachable feed never aborts an ingest run (FR-2). The one exception is
    a missing optional dependency, which is a configuration mistake and says so.
    """

    def __init__(self, *, allow_network: bool | None = None, timeout: float = 10.0) -> None:
        self._allow_network = allow_network
        self.timeout = timeout

    @property
    def network_allowed(self) -> bool:
        if self._allow_network is not None:
            return self._allow_network
        return os.environ.get(ALLOW_NETWORK_ENV, "") == "1"

    @staticmethod
    def _http_client():  # pragma: no cover - exercised only with the live extra
        try:
            import httpx
        except ImportError as exc:
            raise MissingDependencyError(
                "LiveRssFeedSource needs the optional 'live' extra: pip install 'tickerpress[live]'"
            ) from exc
        return httpx

    def fetch(self, feed: Feed, now: datetime) -> FetchResult:
        if not self.network_allowed:
            return FetchResult.failure(
                f"network disabled: set {ALLOW_NETWORK_ENV}=1 to poll {feed.url}"
            )
        scheme = urlsplit(feed.url).scheme.lower()
        if scheme not in {"http", "https"}:
            return FetchResult.failure(
                f"LiveRssFeedSource only polls http(s) urls, got {feed.url!r}"
            )
        if feed.last_polled_at is not None:
            elapsed = ensure_utc(now) - ensure_utc(feed.last_polled_at)
            if elapsed < MIN_POLL_INTERVAL:
                return FetchResult.unchanged(etag=feed.etag, last_modified=feed.last_modified)

        headers = {"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/xml"}
        if feed.etag:
            headers["If-None-Match"] = feed.etag
        if feed.last_modified:
            headers["If-Modified-Since"] = feed.last_modified

        httpx = self._http_client()
        try:  # pragma: no cover - requires network + optional dependency
            response = httpx.get(
                feed.url, headers=headers, timeout=self.timeout, follow_redirects=True
            )
        except Exception as exc:
            return FetchResult.failure(f"fetch failed for {feed.url}: {exc}")

        if response.status_code == 304:  # pragma: no cover - needs a live server
            return FetchResult.unchanged(
                etag=response.headers.get("ETag") or feed.etag,
                last_modified=response.headers.get("Last-Modified") or feed.last_modified,
            )
        if response.status_code >= 400:  # pragma: no cover - needs a live server
            return FetchResult.failure(f"HTTP {response.status_code} for {feed.url}")
        return FetchResult(  # pragma: no cover - needs a live server
            status=FetchStatus.OK,
            raw_bytes=response.content,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )
