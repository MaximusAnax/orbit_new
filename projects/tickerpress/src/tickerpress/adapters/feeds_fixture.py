"""Offline FeedSource: committed XML bytes behind ``file://`` URLs.

This is the default adapter — tests and the whole eval suite run on it, so the
offline path never touches the network (eval hermeticity, M5).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from ..engine.models import Feed, FetchStatus
from .feeds import FetchResult

__all__ = ["FixtureFeedSource"]


class FixtureFeedSource:
    """Resolve a feed URL to committed bytes.

    Resolution order: an explicit in-memory ``documents`` mapping (unit tests),
    then the ``file://`` path itself, then — if that path is missing and
    ``base_dir`` is set — the same file name under ``base_dir``. The last rule
    is what keeps a committed fixture URL (which necessarily embeds somebody's
    absolute path) working in a different checkout. ``etag`` is unused: the
    fixture source is always "ok" and always deterministic.
    """

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        documents: Mapping[str, bytes] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.documents = dict(documents or {})

    def add(self, url: str, raw: bytes) -> None:
        """Register in-memory bytes for a URL (test convenience)."""

        self.documents[url] = raw

    def _resolve_path(self, url: str) -> Path | None:
        parts = urlsplit(url)
        if parts.scheme != "file":
            return None
        path = Path(url2pathname(parts.path))
        if not path.is_file() and self.base_dir is not None:
            relocated = self.base_dir / path.name
            if relocated.is_file():
                return relocated
        return path

    def fetch(self, feed: Feed, now: datetime) -> FetchResult:
        if feed.url in self.documents:
            return FetchResult(status=FetchStatus.OK, raw_bytes=self.documents[feed.url])
        path = self._resolve_path(feed.url)
        if path is None:
            return FetchResult.failure(
                f"FixtureFeedSource cannot serve {feed.url!r}: expected a file:// url"
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return FetchResult.failure(f"cannot read fixture feed {path}: {exc}")
        return FetchResult(status=FetchStatus.OK, raw_bytes=raw)
