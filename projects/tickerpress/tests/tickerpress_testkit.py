"""Shared builders for the tickerpress test suite.

Engine tests construct synthetic :class:`Lexicons` so a feature vector can be
pinned exactly (SCOPE says engine functions take lexicons as an argument
precisely so this is possible); integration tests use the committed ones.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from tickerpress.engine.detect import Candidate, scan
from tickerpress.engine.disambig import THETA, ScoredCandidate, score_article
from tickerpress.engine.models import (
    Alias,
    AliasKind,
    Company,
    DeliveryMode,
    Strength,
)
from tickerpress.engine.normalize import ArticleText, analyze_article_text, normalize_text
from tickerpress.engine.watchlist import Matcher, compile_matcher, default_prior, default_strength
from tickerpress.resources import Lexicons, load_lexicons

NOW = datetime(2026, 3, 2, 13, 0, 0, tzinfo=UTC)

DEFAULT_ABBREVIATIONS = frozenset({"inc.", "corp.", "co.", "ltd.", "u.s.", "dr.", "mr.", "vs."})
DEFAULT_TRACKING = ("utm_*", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "cmpid")
DEFAULT_SUFFIXES = ("inc", "inc.", "corp", "corp.", "co", "co.", "ltd", "plc", "holdings", "group")


def make_lexicons(
    *,
    corporate_cues: Iterable[str] = (),
    anti_cues: Iterable[str] = (),
    common_words: Iterable[str] = (),
    legal_suffixes: Sequence[str] = DEFAULT_SUFFIXES,
    abbreviations: Iterable[str] = DEFAULT_ABBREVIATIONS,
    tracking_params: Sequence[str] = DEFAULT_TRACKING,
    digest_template: str | None = None,
) -> Lexicons:
    """A synthetic lexicon bundle with exactly the entries a test needs."""

    return Lexicons(
        corporate_cues=frozenset(word.casefold() for word in corporate_cues),
        anti_cues=frozenset(word.casefold() for word in anti_cues),
        common_words=frozenset(word.casefold() for word in common_words),
        legal_suffixes=tuple(legal_suffixes),
        abbreviations=frozenset(word.casefold() for word in abbreviations),
        tracking_params=tuple(tracking_params),
        digest_template=(
            digest_template if digest_template is not None else load_lexicons().digest_template
        ),
    )


def make_company(ticker: str, name: str, **kwargs: object) -> Company:
    payload: dict[str, object] = {
        "ticker": ticker,
        "name": name,
        "mode": DeliveryMode.DIGEST,
        "created_at": NOW,
    }
    payload.update(kwargs)
    return Company.model_validate(payload)


def make_alias(
    alias_id: int,
    ticker: str,
    text: str,
    kind: AliasKind,
    lexicons: Lexicons,
    *,
    strength: Strength | None = None,
    prior: float | None = None,
) -> Alias:
    resolved_strength = strength if strength is not None else default_strength(kind, text, lexicons)
    return Alias(
        id=alias_id,
        company_ticker=ticker,
        text=text,
        kind=kind,
        strength=resolved_strength,
        prior=prior if prior is not None else default_prior(kind, resolved_strength),
        generated=False,
        created_at=NOW,
    )


def build_watchlist(
    entries: Sequence[Mapping[str, object]], lexicons: Lexicons
) -> tuple[list[Company], list[Alias]]:
    """Build companies + aliases with ids assigned in creation order.

    ``entries`` items look like::

        {"ticker": "AAPL", "name": "Apple Inc.",
         "aliases": [("Apple", AliasKind.SHORT_NAME), ("AAPL", AliasKind.TICKER_SYMBOL)],
         "context_terms": ["cupertino"]}
    """

    companies: list[Company] = []
    aliases: list[Alias] = []
    next_id = 1
    for entry in entries:
        spec = dict(entry)
        alias_specs = spec.pop("aliases", [])
        ticker = str(spec.pop("ticker"))
        name = str(spec.pop("name"))
        companies.append(make_company(ticker, name, **spec))
        for alias_spec in alias_specs:  # type: ignore[union-attr]
            text, kind, *rest = alias_spec
            strength = rest[0] if rest else None
            prior = rest[1] if len(rest) > 1 else None
            aliases.append(
                make_alias(next_id, ticker, text, kind, lexicons, strength=strength, prior=prior)
            )
            next_id += 1
    return companies, aliases


def compile_watchlist(
    entries: Sequence[Mapping[str, object]], lexicons: Lexicons
) -> tuple[Matcher, dict[str, Company]]:
    companies, aliases = build_watchlist(entries, lexicons)
    return (
        compile_matcher(companies, aliases, lexicons),
        {company.ticker: company for company in companies},
    )


def article_of(
    title: str, summary: str = "", content: str | None = None, *, lexicons: Lexicons
) -> ArticleText:
    """Normalize + analyse raw field text the way ingest does."""

    normalized_content = normalize_text(content) or None
    return analyze_article_text(
        normalize_text(title), normalize_text(summary), normalized_content, lexicons
    )


def detect_in(
    article: ArticleText, entries: Sequence[Mapping[str, object]], lexicons: Lexicons
) -> tuple[Candidate, ...]:
    matcher, _ = compile_watchlist(entries, lexicons)
    return scan(article.fields, matcher)


def score_in(
    article: ArticleText,
    entries: Sequence[Mapping[str, object]],
    lexicons: Lexicons,
    *,
    threshold: float = THETA,
) -> tuple[ScoredCandidate, ...]:
    matcher, companies = compile_watchlist(entries, lexicons)
    return score_article(
        article, scan(article.fields, matcher), companies, lexicons, threshold=threshold
    )


def only(items: Sequence[ScoredCandidate]) -> ScoredCandidate:
    assert len(items) == 1, f"expected exactly one candidate, got {len(items)}: {items}"
    return items[0]


def rss_feed(items: Sequence[str], *, title: str = "Test Wire") -> bytes:
    """Wrap pre-rendered ``<item>`` blocks in an RSS 2.0 document."""

    body = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        f"<channel><title>{title}</title>\n{body}\n</channel></rss>"
    ).encode()


def rss_item(
    *,
    guid: str | None = None,
    link: str | None = None,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
    pub_date: str | None = None,
) -> str:
    parts = ["<item>"]
    if guid is not None:
        parts.append(f"<guid>{guid}</guid>")
    if link is not None:
        parts.append(f"<link>{link}</link>")
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if description is not None:
        parts.append(f"<description>{description}</description>")
    if content is not None:
        parts.append(f"<content:encoded>{content}</content:encoded>")
    if pub_date is not None:
        parts.append(f"<pubDate>{pub_date}</pubDate>")
    parts.append("</item>")
    return "".join(parts)
