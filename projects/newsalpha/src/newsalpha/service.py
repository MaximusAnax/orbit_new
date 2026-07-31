"""Orchestration: repository + adapters + the pure engine.

This layer owns the I/O the engine refuses to do (reading a feed, reading bars,
reading and writing rows) and nothing else -- every rule lives in `engine/`.
The API and CLI call these methods and serialize the result.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from .adapters.marketdata import MarketData
from .adapters.newsfeed import NewsFeed
from .datasets import Datasets
from .engine import backtest as backtest_engine
from .engine import pipeline
from .engine.digest import build_digest
from .engine.normalize import parse_iso
from .engine.revise import latest_revisions, superseded_keys
from .models import (
    BacktestParams,
    BacktestResult,
    BacktestRun,
    Digest,
    PriceBar,
    Signal,
    WatchlistItem,
)
from .store.base import Repository, RepositoryError

ACTIVE_WINDOW_ENV = "NEWSALPHA_ACTIVE_WINDOW_DAYS"


def active_window_days_from_env(environ: dict[str, str] | None = None, *, default: int = 30) -> int:
    """`NEWSALPHA_ACTIVE_WINDOW_DAYS` (FR-15), falling back to the documented default."""
    env = environ if environ is not None else dict(os.environ)
    raw = env.get(ACTIVE_WINDOW_ENV, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{ACTIVE_WINDOW_ENV} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{ACTIVE_WINDOW_ENV} must be >= 1, got {value}")
    return value


class NewsAlphaService:
    """Everything the edges need, with the engine kept pure behind it."""

    def __init__(
        self,
        repository: Repository,
        datasets: Datasets,
        *,
        active_window_days: int | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.repository = repository
        self.datasets = datasets
        self.active_window_days = (
            active_window_days
            if active_window_days is not None
            else active_window_days_from_env(environ, default=datasets.active_window_days)
        )

    # -- init -------------------------------------------------------------- #

    def initialize(self, *, reset: bool = False) -> int:
        """Create the schema and load the validated gazetteer into the store (FR-3)."""
        if reset:
            self.repository.reset()
        else:
            self.repository.initialize()
        return self.repository.replace_assets(list(self.datasets.assets.values()))

    # -- ingest ------------------------------------------------------------ #

    def ingest(
        self,
        feed: NewsFeed,
        *,
        as_of: str,
        since: str | None = None,
        until: str | None = None,
    ) -> pipeline.IngestResult:
        """Fetch, append, recompute the active window, persist (FR-1/FR-15)."""
        end = parse_iso(until) if until else parse_iso(as_of)
        start = parse_iso(since) if since else end - timedelta(days=self.active_window_days)
        raws = feed.fetch(start, end)
        stored = self.repository.list_articles()
        fresh = pipeline.prepare_articles(
            raws, stored, self.datasets, as_of, active_window_days=self.active_window_days
        )
        added = self.repository.add_articles(fresh)

        result = pipeline.recompute(
            [*stored, *fresh],
            self.datasets,
            as_of,
            existing_signals=self.repository.list_signals(),
            existing_aliases=self.repository.list_aliases(),
            active_window_days=self.active_window_days,
        )
        self.repository.replace_derived(
            list(result.clusters), list(result.events), list(result.links)
        )
        self.repository.add_aliases(list(result.new_aliases))
        self.repository.add_signals(list(result.new_signals))
        self.repository.add_briefs(list(result.briefs))

        return pipeline.IngestResult(
            articles_new=added,
            articles_excluded=sum(1 for a in fresh if a.excluded_from_analysis),
            clusters=len(result.clusters),
            events=len(result.events),
            signals_new=sum(1 for s in result.new_signals if s.revision == 1),
            revisions_new=sum(1 for s in result.new_signals if s.revision > 1),
            briefs=len(result.briefs),
        )

    # -- reads ------------------------------------------------------------- #

    def digest(
        self,
        *,
        as_of_date: str,
        watchlist_only: bool = True,
        include_superseded: bool = False,
    ) -> Digest:
        watchlist = {item.asset_id for item in self.repository.list_watchlist()}
        return build_digest(
            self.repository.list_signals(),
            self.datasets,
            as_of_date=as_of_date,
            watchlist=watchlist,
            watchlist_only=watchlist_only,
            include_superseded=include_superseded,
        )

    def signal_revisions(self, signal_id: str) -> list[Signal]:
        signal = self.repository.get_signal(signal_id)
        if signal is None:
            return []
        return self.repository.signals_for_key(signal.signal_key)

    def signals(
        self,
        *,
        asset_id: str | None = None,
        direction: str | None = None,
        min_confidence: float | None = None,
        since: str | None = None,
        include_superseded: bool = False,
        latest_only: bool = True,
    ) -> list[Signal]:
        """Signals for the list surfaces: latest revision per key, superseded hidden.

        FR-8's supersession filter is defined over signal *keys*, so it is applied
        after the repository's row filters -- a key demoted by a newer, different-stage
        signal disappears from the default view but is still stored and still
        backtested (FR-10).
        """
        rows = self.repository.list_signals(
            asset_id=asset_id,
            direction=direction,
            min_confidence=min_confidence,
            since=since,
        )
        if latest_only:
            rows = latest_revisions(rows)
        if not include_superseded:
            demoted = superseded_keys(self.repository.list_signals())
            rows = [s for s in rows if s.signal_key not in demoted]
        return sorted(rows, key=lambda s: (-abs(s.score), -s.confidence, s.asset_id))

    # -- watchlist --------------------------------------------------------- #

    def add_watch(self, asset_id: str, added_at: str) -> bool:
        if asset_id not in self.datasets.assets:
            raise RepositoryError(
                f"unknown asset {asset_id!r}; closest match: {self.suggest_asset(asset_id)}"
            )
        return self.repository.add_watchlist(WatchlistItem(asset_id=asset_id, added_at=added_at))

    def remove_watch(self, asset_id: str) -> bool:
        return self.repository.remove_watchlist(asset_id)

    def suggest_asset(self, needle: str) -> str | None:
        """Nearest gazetteer id for an unknown one (FR-11), by simple edit affinity."""
        candidates = list(self.datasets.assets)
        target = needle.casefold()
        best: tuple[float, str] | None = None
        for candidate in candidates:
            score = _affinity(target, candidate.casefold())
            asset = self.datasets.assets[candidate]
            for surface in (asset.symbol, asset.name, *asset.aliases):
                score = max(score, _affinity(target, surface.casefold()))
            if best is None or score > best[0]:
                best = (score, candidate)
        return best[1] if best is not None and best[0] > 0 else None

    # -- prices ------------------------------------------------------------ #

    def load_prices(
        self,
        market: MarketData,
        *,
        assets: list[str] | None = None,
        start: str,
        end: str,
    ) -> int:
        """Pull bars for the given assets (default: every gazetteer asset) and store them."""
        wanted = assets or list(self.datasets.assets)
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        bars: list[PriceBar] = []
        for asset_id in wanted:
            bars.extend(market.daily_bars(asset_id, start_date, end_date))
        return self.repository.add_price_bars(bars)

    # -- backtests --------------------------------------------------------- #

    def run_backtest(
        self,
        params: BacktestParams,
        as_of: str,
        *,
        persist: bool = True,
    ) -> tuple[BacktestRun, list[BacktestResult]]:
        """Backtest the latest revision of every signal key (FR-10)."""
        all_signals = self.repository.list_signals()
        signals = latest_revisions(all_signals)
        estimated = frozenset(
            article.id
            for article in self.repository.list_articles()
            if article.published_at_estimated
        )
        run, results = backtest_engine.run_backtest(
            signals,
            self.repository.list_price_bars(),
            self.datasets,
            params,
            as_of,
            superseded_keys=superseded_keys(all_signals),
            estimated_article_ids=estimated,
        )
        if persist:
            self.repository.add_backtest(run, results)
        return run, results


def _affinity(left: str, right: str) -> float:
    """Cheap similarity in [0, 1]: shared-prefix length plus containment, normalized."""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    prefix = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        prefix += 1
    contained = 0.5 if left in right or right in left else 0.0
    return min(1.0, prefix / max(len(left), len(right)) + contained)


def window_bounds(as_of: str, days: int) -> tuple[datetime, datetime]:
    end = parse_iso(as_of)
    return end - timedelta(days=days), end


__all__ = [
    "ACTIVE_WINDOW_ENV",
    "NewsAlphaService",
    "active_window_days_from_env",
    "window_bounds",
]
