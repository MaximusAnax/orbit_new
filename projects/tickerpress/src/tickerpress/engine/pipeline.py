"""Ingest orchestration as pure functions (SCOPE FR-3/4/5/6/8).

Everything here is a function of (fetched item, watchlist, lexicons, ``now``):
no clock reads, no filesystem, no network. :mod:`tickerpress.services` supplies
the I/O around it — fetching, the dedup window query, transactions, delivery —
so the whole scoring pipeline stays reproducible from archived bytes alone.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..resources import Lexicons
from . import detect, disambig, relevance
from .dedup import Shingle, shingles_of
from .feedparse import FeedItem
from .models import (
    Appearance,
    Company,
    Mention,
    PublishedSource,
    Strength,
    ensure_utc,
)
from .normalize import ArticleText, analyze_article_text, normalize_text
from .watchlist import Matcher

__all__ = [
    "ArticleAnalysis",
    "PreparedArticle",
    "analyze_article",
    "canonicalize_url",
    "content_hash",
    "prepare_article",
]

_DEFAULT_PORTS = {"http": "80", "https": "443", "ftp": "21"}


def _is_tracking_param(name: str, patterns: Sequence[str]) -> bool:
    folded = name.casefold()
    for pattern in patterns:
        if pattern.endswith("*"):
            if folded.startswith(pattern[:-1]):
                return True
        elif folded == pattern:
            return True
    return False


def canonicalize_url(url: str, tracking_params: Sequence[str]) -> str:
    """Canonical form per FR-4.

    Lower-cased scheme and host, default port dropped, fragment dropped,
    tracking parameters removed (the Urchin family plus the social click ids),
    remaining query parameters sorted. These vary per syndication channel and
    would otherwise defeat both article identity and the dedup fast path.
    """

    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if not parts.scheme and not parts.netloc:
        return raw

    userinfo, _, hostport = parts.netloc.rpartition("@")
    host, _, port = hostport.partition(":")
    host = host.casefold()
    scheme = parts.scheme.casefold()
    if port and port == _DEFAULT_PORTS.get(scheme):
        port = ""
    netloc = host + (f":{port}" if port else "")
    if userinfo:
        netloc = f"{userinfo}@{netloc}"

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key, tracking_params)
    ]
    query = urlencode(sorted(kept), doseq=False)
    return urlunsplit((scheme, netloc, parts.path, query, ""))


def content_hash(title: str, summary: str, content: str | None) -> str:
    """sha256 over normalized ``title + "\\n" + summary + "\\n" + content``."""

    payload = f"{title}\n{summary}\n{content or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedArticle:
    """A feed item normalized into archive shape, plus its analysed text."""

    item_guid: str
    url: str
    canonical_url: str
    title: str
    summary: str
    content: str | None
    published_at: datetime
    published_source: PublishedSource
    content_sha256: str
    token_count: int
    content_token_count: int
    text: ArticleText
    shingles: frozenset[Shingle]


def prepare_article(item: FeedItem, now: datetime, lexicons: Lexicons) -> PreparedArticle:
    """Normalize one feed item (FR-3, FR-4).

    A missing or unparseable ``pubDate`` falls back to the ingest ``now`` and is
    recorded as ``published_source="fallback"`` rather than silently invented.
    """

    title = normalize_text(item.title)
    summary = normalize_text(item.description)
    content: str | None = normalize_text(item.content) or None

    text = analyze_article_text(title, summary, content, lexicons)
    if text.content is not None and text.content.token_count == 0:
        content = None
        text = analyze_article_text(title, summary, None, lexicons)

    published_at = item.published_at
    source = PublishedSource.FEED if published_at is not None else PublishedSource.FALLBACK
    published_at = ensure_utc(published_at if published_at is not None else now)

    url = (item.link or "").strip()
    return PreparedArticle(
        item_guid=item.identity,
        url=url,
        canonical_url=canonicalize_url(url, lexicons.tracking_params),
        title=title,
        summary=summary,
        content=content,
        published_at=published_at,
        published_source=source,
        content_sha256=content_hash(title, summary, content),
        token_count=text.token_count,
        content_token_count=text.content_token_count,
        text=text,
        shingles=shingles_of(text),
    )


@dataclass(frozen=True)
class ArticleAnalysis:
    """Every row one article contributes to the archive, in write order."""

    mentions: tuple[Mention, ...]
    appearances: tuple[Appearance, ...]

    @property
    def candidates_total(self) -> int:
        return len(self.mentions)

    @property
    def mentions_accepted(self) -> int:
        return sum(1 for mention in self.mentions if mention.accepted)


def analyze_article(
    article_id: int,
    prepared: PreparedArticle,
    matcher: Matcher,
    companies: Mapping[str, Company],
    lexicons: Lexicons,
    *,
    engine_version: str,
    threshold: float = disambig.THETA,
) -> ArticleAnalysis:
    """Detect, disambiguate and roll up one article (FR-5, FR-6, FR-8)."""

    candidates = detect.scan(prepared.text.fields, matcher)
    scored = disambig.score_article(
        prepared.text, candidates, companies, lexicons, threshold=threshold
    )

    mentions: list[Mention] = []
    accepted_by_company: dict[str, list[detect.Candidate]] = {}
    for result in scored:
        candidate = result.candidate
        mentions.append(
            Mention(
                article_id=article_id,
                company_ticker=candidate.company_ticker,
                alias_id=candidate.alias_id,
                field=candidate.field,
                char_start=candidate.char_start,
                char_end=candidate.char_end,
                surface=candidate.surface,
                matched_via=candidate.matched_via,
                strength=candidate.strength,
                features={} if result.features is None else result.features.as_dict(),
                score=result.score,
                threshold=result.threshold,
                accepted=result.accepted,
                engine_version=engine_version,
            )
        )
        if result.accepted:
            accepted_by_company.setdefault(candidate.company_ticker, []).append(candidate)

    appearances: list[Appearance] = []
    for ticker in sorted(accepted_by_company):
        components = relevance.compute_components(accepted_by_company[ticker], prepared.text)
        if components is None:  # pragma: no cover - the bucket is never empty
            continue
        appearances.append(
            Appearance(
                article_id=article_id,
                company_ticker=ticker,
                mention_count=components.mention_count,
                title_hit=components.title_hit,
                lede_hit=components.lede_hit,
                relevance=components.relevance,
            )
        )

    return ArticleAnalysis(mentions=tuple(mentions), appearances=tuple(appearances))


def strong_companies(analysis: ArticleAnalysis) -> tuple[str, ...]:
    """Tickers with at least one strong accepted mention (explain/debug aid)."""

    return tuple(
        sorted(
            {
                mention.company_ticker
                for mention in analysis.mentions
                if mention.strength is Strength.STRONG and mention.accepted
            }
        )
    )
