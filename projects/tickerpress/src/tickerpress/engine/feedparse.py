"""RSS 2.0 / Atom (RFC 4287) subset parser (SCOPE FR-3).

Pure: bytes in, :class:`FeedItem` values out. The same parser runs behind the
fixture adapter and the live adapter, so the offline suite exercises exactly the
code path that production uses — only the fetch differs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from .models import ensure_utc

__all__ = [
    "FeedItem",
    "FeedParseError",
    "ParsedFeed",
    "SkippedItem",
    "item_identity",
    "parse_feed",
]

_ATOM = "http://www.w3.org/2005/Atom"
_CONTENT = "http://purl.org/rss/1.0/modules/content/"
_DC = "http://purl.org/dc/elements/1.1/"


class FeedParseError(ValueError):
    """Raised when a feed document cannot be parsed at all (malformed XML)."""


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One raw feed entry, before normalization.

    ``position`` is the item's index in document order (FR-3), which — with
    ascending feed-id iteration (FR-2) — makes archived article ids a pure
    function of the fixture bytes.
    """

    position: int
    guid: str | None = None
    link: str | None = None
    title: str | None = None
    description: str | None = None
    content: str | None = None
    published_at: datetime | None = None

    @property
    def identity(self) -> str:
        """RSS ``guid`` / ``atom:id``, else ``sha256(link + "\\n" + title)``."""

        return item_identity(self.guid, self.link, self.title)


@dataclass(frozen=True, slots=True)
class SkippedItem:
    """An entry that could not be archived, with the reason recorded."""

    position: int
    reason: str


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    kind: str
    items: tuple[FeedItem, ...] = ()
    skipped: tuple[SkippedItem, ...] = field(default=())


def item_identity(guid: str | None, link: str | None, title: str | None) -> str:
    """Item identity per FR-3 — real feeds routinely omit ``guid``."""

    if guid and guid.strip():
        return guid.strip()
    payload = f"{(link or '').strip()}\n{(title or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext())
    text = text.strip()
    return text or None


def _parse_rfc822(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return ensure_utc(parsed) if parsed is not None else None


def _parse_rfc3339(value: str) -> datetime | None:
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        return ensure_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _parse_date(value: str | None) -> datetime | None:
    """Accept RFC 822 (RSS ``pubDate``) or RFC 3339 (Atom, ``dc:date``)."""

    if not value:
        return None
    return _parse_rfc822(value) or _parse_rfc3339(value)


def _atom_link(entry: ElementTree.Element) -> str | None:
    fallback: str | None = None
    for link in entry.findall(f"{{{_ATOM}}}link"):
        href = link.get("href")
        if not href:
            continue
        rel = link.get("rel")
        if rel in (None, "alternate"):
            return href.strip()
        if fallback is None:
            fallback = href.strip()
    return fallback


def _parse_rss(root: ElementTree.Element) -> ParsedFeed:
    channel = root.find("channel")
    container = channel if channel is not None else root
    items: list[FeedItem] = []
    skipped: list[SkippedItem] = []
    for position, node in enumerate(container.findall("item")):
        link = _text(node.find("link"))
        title = _text(node.find("title"))
        if not link and not title:
            skipped.append(SkippedItem(position=position, reason="item has neither link nor title"))
            continue
        published = _parse_date(_text(node.find("pubDate")) or _text(node.find(f"{{{_DC}}}date")))
        items.append(
            FeedItem(
                position=position,
                guid=_text(node.find("guid")),
                link=link,
                title=title,
                description=_text(node.find("description")),
                content=_text(node.find(f"{{{_CONTENT}}}encoded")),
                published_at=published,
            )
        )
    return ParsedFeed(kind="rss", items=tuple(items), skipped=tuple(skipped))


def _parse_atom(root: ElementTree.Element) -> ParsedFeed:
    items: list[FeedItem] = []
    skipped: list[SkippedItem] = []
    for position, entry in enumerate(root.findall(f"{{{_ATOM}}}entry")):
        link = _atom_link(entry)
        title = _text(entry.find(f"{{{_ATOM}}}title"))
        if not link and not title:
            skipped.append(
                SkippedItem(position=position, reason="entry has neither link nor title")
            )
            continue
        published = _parse_date(
            _text(entry.find(f"{{{_ATOM}}}published")) or _text(entry.find(f"{{{_ATOM}}}updated"))
        )
        items.append(
            FeedItem(
                position=position,
                guid=_text(entry.find(f"{{{_ATOM}}}id")),
                link=link,
                title=title,
                description=_text(entry.find(f"{{{_ATOM}}}summary")),
                content=_text(entry.find(f"{{{_ATOM}}}content")),
                published_at=published,
            )
        )
    return ParsedFeed(kind="atom", items=tuple(items), skipped=tuple(skipped))


def parse_feed(raw: bytes) -> ParsedFeed:
    """Parse RSS 2.0 or Atom bytes into items in document order.

    Raises :class:`FeedParseError` on malformed XML or an unrecognised root
    element; ingest turns that into a per-feed error and carries on (FR-2).
    """

    if not raw or not raw.strip():
        raise FeedParseError("empty feed document")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise FeedParseError(f"malformed feed XML: {exc}") from exc

    tag = root.tag
    if tag == "rss" or (tag == "channel" and root.find("item") is not None):
        return _parse_rss(root)
    if tag == f"{{{_ATOM}}}feed":
        return _parse_atom(root)
    raise FeedParseError(f"unsupported feed root element {tag!r}: expected <rss> or Atom <feed>")
