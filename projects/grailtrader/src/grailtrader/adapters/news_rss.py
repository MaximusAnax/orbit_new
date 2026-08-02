"""Live ``NewsFeed``: keyword-rule candidate extraction over fashion-news RSS (US-7).

Activates only when ``GRAILTRADER_NEWS_FEEDS`` (a comma-separated list of feed
URLs, e.g. Business of Fashion / Vogue Business / Hypebeast / WWD) is set, and
needs the optional ``feedparser`` dependency (extra ``live``). Everything it
emits is a ``pending`` **candidate**: a pending event has zero effect on impacts,
advice or backtests until ``events review --confirm`` promotes it (FR-5, SCOPE
non-goal 5 — no NLP extraction, a human is the QA).

This module is never imported on the test/eval path (test T2 asserts it); the
classifier itself is a pure function and is unit-tested directly.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..engine.events import make_event
from ..engine.strata import Gazetteer
from ..models import EventSource, EventStatus, EventType, FashionEvent

__all__ = ["FEEDS_ENV", "KEYWORD_RULES", "KeywordRule", "RssNewsFeed", "classify_headline"]

#: Comma-separated feed URLs. Absent -> the adapter refuses to run.
FEEDS_ENV = "GRAILTRADER_NEWS_FEEDS"


@dataclass(frozen=True)
class KeywordRule:
    """One committed keyword rule: phrases -> a typed candidate event."""

    phrases: tuple[str, ...]
    event_type: EventType
    attributes: dict[str, Any] = field(default_factory=dict)
    needs_closing_era: bool = False


#: The committed keyword-rule table. Order matters: the first match wins, so the
#: most specific phrases (deaths, ousters) sit above the generic ones.
KEYWORD_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        ("has died", "dies at", "passed away", "death of"),
        EventType.DESIGNER_DEPARTURE,
        {"reason": "death"},
        True,
    ),
    KeywordRule(
        (
            "shuts down",
            "shutting down",
            "to close",
            "closes its doors",
            "ceases operations",
            "winds down",
        ),
        EventType.DESIGNER_DEPARTURE,
        {"reason": "house_closure"},
        True,
    ),
    KeywordRule(
        ("ousted", "fired", "dismissed", "exits abruptly", "forced out"),
        EventType.DESIGNER_DEPARTURE,
        {"reason": "ousted"},
        True,
    ),
    KeywordRule(
        (
            "steps down",
            "stepping down",
            "resigns",
            "to depart",
            "departs",
            "leaves the house",
            "exits",
        ),
        EventType.DESIGNER_DEPARTURE,
        {"reason": "resignation"},
        True,
    ),
    KeywordRule(
        (
            "named creative director",
            "appointed creative director",
            "new creative director",
            "joins as creative director",
            "takes the helm",
            "named artistic director",
        ),
        EventType.DESIGNER_APPOINTMENT,
        {"acclaim": "neutral"},
    ),
    KeywordRule(
        ("collaboration", "collab", "teams up with", "partners with", "x drop"),
        EventType.COLLAB_ANNOUNCEMENT,
        {},
    ),
    KeywordRule(
        (
            "scandal",
            "backlash",
            "apologises",
            "apologizes",
            "controversy",
            "campaign pulled",
            "boycott",
        ),
        EventType.BRAND_SCANDAL,
        {"severity": "moderate"},
    ),
    KeywordRule(
        ("panned", "flops", "criticised", "criticized", "worst show"),
        EventType.RUNWAY_RECEPTION,
        {"polarity": "panned"},
    ),
    KeywordRule(
        ("triumph", "acclaimed", "best show", "standing ovation", "rapturous"),
        EventType.RUNWAY_RECEPTION,
        {"polarity": "acclaimed"},
    ),
    KeywordRule(
        ("wore", "wears", "spotted in", "steps out in", "red carpet in"),
        EventType.CELEBRITY_COSIGN,
        {"tier": "b_list", "celebrity": "unnamed"},
    ),
)


def classify_headline(
    headline: str, gazetteer: Gazetteer
) -> tuple[str, EventType, dict[str, Any], str | None] | None:
    """Classify a headline into ``(brand_id, event_type, attributes, closing_era_id)``.

    Pure and offline: this is the whole of the live adapter's "extraction". It
    returns ``None`` when no brand or no rule matches — the adapter never guesses.
    """
    lowered = headline.casefold()
    brand_id = _match_brand(lowered, gazetteer)
    if brand_id is None:
        return None
    for rule in KEYWORD_RULES:
        if not any(phrase in lowered for phrase in rule.phrases):
            continue
        era_id: str | None = None
        if rule.needs_closing_era:
            era_id = _closing_era(brand_id, gazetteer)
            if era_id is None:
                continue
        attributes = dict(rule.attributes)
        if rule.event_type is EventType.COLLAB_ANNOUNCEMENT:
            attributes.setdefault("counterparty", "unnamed")
        if rule.event_type is EventType.DESIGNER_APPOINTMENT:
            attributes.setdefault("designer", "unnamed")
        return brand_id, rule.event_type, attributes, era_id
    return None


def _match_brand(lowered_headline: str, gazetteer: Gazetteer) -> str | None:
    best: tuple[int, str] | None = None
    for brand in gazetteer.brands:
        for alias in (brand.name, *brand.aliases):
            folded = alias.casefold()
            if (
                len(folded) >= 3
                and folded in lowered_headline
                and (best is None or len(folded) > best[0])
            ):
                best = (len(folded), brand.id)
    return None if best is None else best[1]


def _closing_era(brand_id: str, gazetteer: Gazetteer) -> str | None:
    eras = sorted(gazetteer.eras_for(brand_id), key=lambda era: era.start)
    if not eras:
        return None
    for era in eras:
        if era.end is None:
            return era.id
    return eras[-1].id


class RssNewsFeed:
    """Turns configured RSS feeds into ``pending`` candidate events."""

    default_source = EventSource.NEWS

    def __init__(
        self,
        gazetteer: Gazetteer,
        *,
        feeds: Sequence[str] | None = None,
        parser: Any | None = None,
    ) -> None:
        self.gazetteer = gazetteer
        self.feeds = tuple(feeds) if feeds is not None else self.configured_feeds()
        self._parser = parser

    @staticmethod
    def configured_feeds() -> tuple[str, ...]:
        raw = os.environ.get(FEEDS_ENV, "")
        return tuple(url.strip() for url in raw.split(",") if url.strip())

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls.configured_feeds())

    def _feedparser(self) -> Any:
        if self._parser is not None:
            return self._parser
        try:
            import feedparser
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "RssNewsFeed needs the optional 'feedparser' dependency: "
                "install grailtrader with the 'live' extra."
            ) from exc
        return feedparser

    def fetch(self, since: str, until: str) -> list[FashionEvent]:
        """Fetch every configured feed and emit keyword-matched pending candidates."""
        if not self.feeds:
            raise RuntimeError(
                f"RssNewsFeed is not configured: set {FEEDS_ENV} to a comma-separated "
                "list of RSS feed URLs."
            )
        parser = self._feedparser()
        entries: list[tuple[str, str, str]] = []
        for url in self.feeds:
            parsed = parser.parse(url)
            for entry in getattr(parsed, "entries", []):
                published = _entry_date(entry)
                if published is None:
                    continue
                title = str(getattr(entry, "title", "")).strip()
                link = str(getattr(entry, "link", "") or getattr(entry, "id", "") or url)
                if title:
                    entries.append((published, title, link))
        return self.candidates(entries, since=since, until=until)

    def candidates(
        self, entries: Iterable[tuple[str, str, str]], *, since: str, until: str
    ) -> list[FashionEvent]:
        """Turn ``(published_iso_date, headline, link)`` triples into pending candidates."""
        out: list[FashionEvent] = []
        for published, title, link in entries:
            if not since <= published <= until:
                continue
            classified = classify_headline(title, self.gazetteer)
            if classified is None:
                continue
            brand_id, event_type, attributes, era_id = classified
            out.append(
                make_event(
                    event_type=event_type,
                    brand_id=brand_id,
                    occurred_on=published,
                    source=EventSource.NEWS,
                    source_refs=(link,),
                    status=EventStatus.PENDING,
                    era_id=era_id,
                    attributes=attributes,
                    notes=title,
                )
            )
        out.sort(key=lambda event: (event.occurred_on, event.id))
        return out


def _entry_date(entry: Any) -> str | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed is not None:
        return date(parsed[0], parsed[1], parsed[2]).isoformat()
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            return None
    return None
