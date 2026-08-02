"""In-memory `Repository` -- the backend tests and evals use.

Enforces exactly the invariants the SQLite schema enforces with constraints, so a
test that passes here would pass there: append-only articles/signals/briefs,
unique (source_domain, external_id) and content_hash, one brief per revision,
fixture bars never overwritten by live loads.
"""

from __future__ import annotations

from ..models import (
    Article,
    Asset,
    BacktestResult,
    BacktestRun,
    BarSource,
    Brief,
    Cluster,
    Event,
    EventLink,
    PriceBar,
    Signal,
    SignalKeyAlias,
    WatchlistItem,
)
from .base import RepositoryError


class InMemoryRepository:
    """A dict-backed repository. Cheap to construct; nothing is shared between instances."""

    def __init__(self) -> None:
        self.reset()

    # -- schema ------------------------------------------------------------ #

    def initialize(self) -> None:
        return None

    def reset(self) -> None:
        self._assets: dict[str, Asset] = {}
        self._watchlist: dict[str, WatchlistItem] = {}
        self._articles: dict[str, Article] = {}
        self._article_keys: set[tuple[str, str]] = set()
        self._article_hashes: set[str] = set()
        self._clusters: dict[str, Cluster] = {}
        self._events: dict[str, Event] = {}
        self._links: list[EventLink] = []
        self._signals: dict[str, Signal] = {}
        self._aliases: dict[str, SignalKeyAlias] = {}
        self._briefs: dict[str, Brief] = {}
        self._bars: dict[tuple[str, str], PriceBar] = {}
        self._runs: dict[str, BacktestRun] = {}
        self._results: dict[str, list[BacktestResult]] = {}

    def close(self) -> None:
        return None

    # -- assets & watchlist ------------------------------------------------ #

    def replace_assets(self, assets: list[Asset]) -> int:
        self._assets = {asset.id: asset for asset in assets}
        return len(self._assets)

    def get_asset(self, asset_id: str) -> Asset | None:
        return self._assets.get(asset_id)

    def list_assets(self, *, kind: str | None = None, query: str | None = None) -> list[Asset]:
        rows = list(self._assets.values())
        if kind is not None:
            rows = [a for a in rows if a.kind.value == kind]
        if query:
            needle = query.casefold()
            rows = [
                a
                for a in rows
                if needle in a.id.casefold()
                or needle in a.name.casefold()
                or needle in a.symbol.casefold()
                or any(needle in alias.casefold() for alias in a.aliases)
            ]
        return sorted(rows, key=lambda a: a.id)

    def add_watchlist(self, item: WatchlistItem) -> bool:
        if item.asset_id not in self._assets:
            raise RepositoryError(f"unknown asset {item.asset_id!r}")
        if item.asset_id in self._watchlist:
            return False
        self._watchlist[item.asset_id] = item
        return True

    def remove_watchlist(self, asset_id: str) -> bool:
        return self._watchlist.pop(asset_id, None) is not None

    def list_watchlist(self) -> list[WatchlistItem]:
        return sorted(self._watchlist.values(), key=lambda item: item.asset_id)

    # -- articles ---------------------------------------------------------- #

    def add_articles(self, articles: list[Article]) -> int:
        added = 0
        for article in articles:
            key = (article.source_domain, article.external_id)
            if article.id in self._articles:
                continue
            if key in self._article_keys or article.content_hash in self._article_hashes:
                continue
            self._articles[article.id] = article
            self._article_keys.add(key)
            self._article_hashes.add(article.content_hash)
            added += 1
        return added

    def get_article(self, article_id: str) -> Article | None:
        return self._articles.get(article_id)

    def list_articles(
        self,
        *,
        since: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        rows = list(self._articles.values())
        if since is not None:
            rows = [a for a in rows if a.published_at >= since]
        if domain is not None:
            rows = [a for a in rows if a.source_domain == domain.lower()]
        rows.sort(key=lambda a: (a.published_at, a.id))
        return rows[:limit] if limit else rows

    # -- derived rows ------------------------------------------------------ #

    def replace_derived(
        self,
        clusters: list[Cluster],
        events: list[Event],
        links: list[EventLink],
    ) -> None:
        self._clusters = {cluster.id: cluster for cluster in clusters}
        self._events = {event.id: event for event in events}
        self._links = list(links)

    def list_clusters(self) -> list[Cluster]:
        return sorted(self._clusters.values(), key=lambda c: c.id)

    def get_event(self, event_id: str) -> Event | None:
        return self._events.get(event_id)

    def list_events(
        self,
        *,
        event_type: str | None = None,
        asset_id: str | None = None,
        stage: str | None = None,
        since: str | None = None,
    ) -> list[Event]:
        rows = list(self._events.values())
        if event_type is not None:
            rows = [e for e in rows if e.event_type.value == event_type]
        if stage is not None:
            rows = [e for e in rows if e.stage.value == stage]
        if since is not None:
            rows = [e for e in rows if e.event_date >= since]
        if asset_id is not None:
            linked = {link.event_id for link in self._links if link.asset_id == asset_id}
            rows = [e for e in rows if e.id in linked]
        return sorted(rows, key=lambda e: (e.event_date, e.id))

    def list_links(self, event_id: str | None = None) -> list[EventLink]:
        rows = [link for link in self._links if event_id is None or link.event_id == event_id]
        return sorted(rows, key=lambda link: (link.event_id, link.asset_id))

    # -- signals ----------------------------------------------------------- #

    def add_signals(self, signals: list[Signal]) -> int:
        added = 0
        for signal in signals:
            if signal.id in self._signals:
                continue
            clash = [
                s
                for s in self._signals.values()
                if s.signal_key == signal.signal_key and s.revision == signal.revision
            ]
            if clash:
                raise RepositoryError(
                    f"signal {signal.signal_key} already has revision {signal.revision}"
                )
            self._signals[signal.id] = signal
            added += 1
        return added

    def get_signal(self, signal_id: str) -> Signal | None:
        return self._signals.get(signal_id)

    def list_signals(
        self,
        *,
        asset_id: str | None = None,
        direction: str | None = None,
        min_confidence: float | None = None,
        since: str | None = None,
    ) -> list[Signal]:
        rows = list(self._signals.values())
        if asset_id is not None:
            rows = [s for s in rows if s.asset_id == asset_id]
        if direction is not None:
            rows = [s for s in rows if s.direction.value == direction]
        if min_confidence is not None:
            rows = [s for s in rows if s.confidence >= min_confidence]
        if since is not None:
            rows = [s for s in rows if s.event_snapshot.event_date >= since]
        return sorted(rows, key=lambda s: (s.signal_key, s.revision))

    def signals_for_key(self, signal_key: str) -> list[Signal]:
        return sorted(
            (s for s in self._signals.values() if s.signal_key == signal_key),
            key=lambda s: s.revision,
        )

    def add_aliases(self, aliases: list[SignalKeyAlias]) -> int:
        added = 0
        for alias in aliases:
            if alias.from_key in self._aliases:
                continue
            self._aliases[alias.from_key] = alias
            added += 1
        return added

    def list_aliases(self) -> list[SignalKeyAlias]:
        return sorted(self._aliases.values(), key=lambda a: a.from_key)

    # -- briefs ------------------------------------------------------------ #

    def add_briefs(self, briefs: list[Brief]) -> int:
        added = 0
        for brief in briefs:
            if brief.signal_id in self._briefs:
                continue
            if brief.signal_id not in self._signals:
                raise RepositoryError(f"brief references unknown signal {brief.signal_id!r}")
            self._briefs[brief.signal_id] = brief
            added += 1
        return added

    def get_brief(self, signal_id: str) -> Brief | None:
        return self._briefs.get(signal_id)

    # -- prices ------------------------------------------------------------ #

    def add_price_bars(self, bars: list[PriceBar]) -> int:
        added = 0
        for bar in bars:
            key = (bar.asset_id, bar.date)
            existing = self._bars.get(key)
            if existing is not None:
                if existing.source is BarSource.fixture and bar.source is BarSource.live:
                    continue  # fixture bars are never overwritten by live loads
                if existing == bar:
                    continue
            self._bars[key] = bar
            added += 1
        return added

    def list_price_bars(
        self,
        *,
        asset_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[PriceBar]:
        rows = list(self._bars.values())
        if asset_id is not None:
            rows = [b for b in rows if b.asset_id == asset_id]
        if start is not None:
            rows = [b for b in rows if b.date >= start]
        if end is not None:
            rows = [b for b in rows if b.date <= end]
        return sorted(rows, key=lambda b: (b.asset_id, b.date))

    # -- backtests --------------------------------------------------------- #

    def add_backtest(self, run: BacktestRun, results: list[BacktestResult]) -> None:
        self._runs[run.id] = run
        self._results[run.id] = list(results)

    def get_backtest(self, run_id: str) -> BacktestRun | None:
        return self._runs.get(run_id)

    def list_backtests(self, limit: int | None = None) -> list[BacktestRun]:
        rows = sorted(self._runs.values(), key=lambda r: (r.as_of, r.id), reverse=True)
        return rows[:limit] if limit else rows

    def list_backtest_results(self, run_id: str) -> list[BacktestResult]:
        return sorted(self._results.get(run_id, []), key=lambda r: r.signal_id)


__all__ = ["InMemoryRepository"]
