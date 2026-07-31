"""FR-14/FR-15: the active-window recompute.

The derived state is a pure function of (stored in-window articles, committed
datasets, `as_of`).  Every ingest run:

  1. appends new articles (idempotent per FR-1);
  2. **recomputes** clustering, extraction, linking and scoring over the *active
     window* -- every stored article with `published_at >= as_of -
     active_window_days` -- not only the new batch;
  3. plans the signal revisions and renders their briefs.

Nothing here reads a clock, the filesystem or the network: `as_of` is an input
and articles arrive as values.  `service.NewsAlphaService` does the I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ..datasets import Datasets
from ..models import (
    Article,
    Brief,
    Cluster,
    Event,
    EventLink,
    RawArticle,
    Signal,
    SignalKeyAlias,
)
from .brief import render_brief
from .cluster import build_clusters
from .extract import build_events
from .link import apply_links
from .normalize import content_hash, hex16, iso_utc, normalize_text, parse_iso
from .revise import RevisionPlan, plan_revisions
from .score import ScoredTuple, score_event

DEFAULT_ACTIVE_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class RecomputeResult:
    """Everything one active-window recompute derived."""

    clusters: tuple[Cluster, ...] = ()
    events: tuple[Event, ...] = ()
    links: tuple[EventLink, ...] = ()
    scored: tuple[ScoredTuple, ...] = ()
    new_signals: tuple[Signal, ...] = ()
    new_aliases: tuple[SignalKeyAlias, ...] = ()
    briefs: tuple[Brief, ...] = ()
    notes: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Counts the API and CLI report back (FR-12/FR-13)."""

    articles_new: int
    articles_excluded: int
    clusters: int
    events: int
    signals_new: int
    revisions_new: int
    briefs: int


def normalize_article(
    raw: RawArticle,
    datasets: Datasets,
    as_of: str,
    *,
    active_window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
) -> Article:
    """FR-1 normalization: strip, hash, tier, and the archive-only window flag."""
    title = normalize_text(raw.title)
    body = normalize_text(raw.body)
    digest = content_hash(title, body)
    cutoff = parse_iso(as_of) - timedelta(days=active_window_days)
    return Article(
        id=hex16(f"{raw.source_domain}|{raw.external_id}|{digest}"),
        external_id=raw.external_id,
        url=raw.url,
        source_domain=raw.source_domain,
        tier=datasets.tier_for(raw.source_domain),
        published_at=iso_utc(parse_iso(raw.published_at)),
        published_at_estimated=raw.published_at_estimated,
        excluded_from_analysis=parse_iso(raw.published_at) < cutoff,
        fetched_at=iso_utc(parse_iso(raw.fetched_at)),
        title=title,
        body=body,
        content_hash=digest,
    )


def prepare_articles(
    raws: list[RawArticle],
    existing: list[Article],
    datasets: Datasets,
    as_of: str,
    *,
    active_window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
) -> list[Article]:
    """Normalize and de-duplicate a batch: re-ingesting an id or hash is a no-op (FR-1)."""
    seen_hashes = {a.content_hash for a in existing}
    seen_keys = {(a.source_domain, a.external_id) for a in existing}
    fresh: list[Article] = []
    for raw in sorted(raws, key=lambda r: (r.published_at, r.external_id)):
        article = normalize_article(raw, datasets, as_of, active_window_days=active_window_days)
        key = (article.source_domain, article.external_id)
        if article.content_hash in seen_hashes or key in seen_keys:
            continue
        seen_hashes.add(article.content_hash)
        seen_keys.add(key)
        fresh.append(article)
    return fresh


def active_window(
    articles: list[Article],
    as_of: str,
    *,
    active_window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
) -> list[Article]:
    """Stored articles eligible for analysis (FR-15): in-window and not archive-only."""
    cutoff = parse_iso(as_of) - timedelta(days=active_window_days)
    return sorted(
        (
            a
            for a in articles
            if not a.excluded_from_analysis and parse_iso(a.published_at) >= cutoff
        ),
        key=lambda a: (a.published_at, a.id),
    )


def recompute(
    articles: list[Article],
    datasets: Datasets,
    as_of: str,
    *,
    existing_signals: list[Signal] | None = None,
    existing_aliases: list[SignalKeyAlias] | None = None,
    active_window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
    render_briefs: bool = True,
) -> RecomputeResult:
    """Cluster -> extract -> link -> score -> revise -> brief, over the active window."""
    in_window = active_window(articles, as_of, active_window_days=active_window_days)
    by_id = {a.id: a for a in in_window}

    clusters = build_clusters(in_window)
    clusters_by_id = {c.id: c for c in clusters}
    extracted = build_events(clusters, by_id, datasets)

    events: list[Event] = []
    links: list[EventLink] = []
    scored: list[ScoredTuple] = []
    notes: dict[str, tuple[str, ...]] = {}

    for item in extracted:
        event, event_links = apply_links(item, by_id, datasets)
        cluster = clusters_by_id[event.cluster_id]
        event_scored, score_notes = score_event(event, event_links, cluster, datasets)
        if score_notes:
            event = event.model_copy(update={"notes": tuple([*event.notes, *score_notes])})
        events.append(event)
        links.extend(event_links)
        scored.extend(event_scored)
        if event.notes:
            notes[event.id] = event.notes

    plan: RevisionPlan = plan_revisions(
        scored,
        list(existing_signals or []),
        list(existing_aliases or []),
        datasets,
        as_of,
    )

    briefs: tuple[Brief, ...] = ()
    if render_briefs:
        events_by_id = {e.id: e for e in events}
        briefs = tuple(
            render_brief(signal, events_by_id[signal.event_id], by_id, datasets)
            for signal in plan.new_signals
        )

    return RecomputeResult(
        clusters=tuple(clusters),
        events=tuple(sorted(events, key=lambda e: e.id)),
        links=tuple(sorted(links, key=lambda link: (link.event_id, link.asset_id))),
        scored=tuple(scored),
        new_signals=plan.new_signals,
        new_aliases=plan.new_aliases,
        briefs=briefs,
        notes=notes,
    )


def ingest(
    raws: list[RawArticle],
    datasets: Datasets,
    as_of: str,
    *,
    existing_articles: list[Article] | None = None,
    existing_signals: list[Signal] | None = None,
    existing_aliases: list[SignalKeyAlias] | None = None,
    active_window_days: int = DEFAULT_ACTIVE_WINDOW_DAYS,
) -> tuple[list[Article], RecomputeResult]:
    """One full ingest run as a pure function: (new articles, recomputed state)."""
    stored = list(existing_articles or [])
    fresh = prepare_articles(raws, stored, datasets, as_of, active_window_days=active_window_days)
    result = recompute(
        [*stored, *fresh],
        datasets,
        as_of,
        existing_signals=existing_signals,
        existing_aliases=existing_aliases,
        active_window_days=active_window_days,
    )
    return fresh, result


__all__ = [
    "DEFAULT_ACTIVE_WINDOW_DAYS",
    "IngestResult",
    "RecomputeResult",
    "active_window",
    "ingest",
    "normalize_article",
    "prepare_articles",
    "recompute",
]
