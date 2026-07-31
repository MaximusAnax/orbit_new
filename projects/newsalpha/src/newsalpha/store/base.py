"""The `Repository` interface.

Two storage classes, per DATA_MODEL.md:

* **durable, append-only** -- `article`, `signal`, `signal_key_alias`, `brief`,
  `price_bar`, `backtest_run`, `backtest_result`, and `watchlist_item` (the one
  user-deletable entity);
* **derived** -- `cluster`, `cluster_member`, `event`, `event_link`: deleted and
  re-inserted for the active window by every recompute, and by no other path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import (
    Article,
    Asset,
    BacktestResult,
    BacktestRun,
    Brief,
    Cluster,
    Event,
    EventLink,
    PriceBar,
    Signal,
    SignalKeyAlias,
    WatchlistItem,
)


class RepositoryError(RuntimeError):
    """A write violated an append-only or uniqueness invariant."""


@runtime_checkable
class Repository(Protocol):
    """Everything the services need; both backends implement exactly this."""

    # -- schema ----------------------------------------------------------- #
    def initialize(self) -> None: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...

    # -- assets & watchlist ------------------------------------------------ #
    def replace_assets(self, assets: list[Asset]) -> int: ...
    def get_asset(self, asset_id: str) -> Asset | None: ...
    def list_assets(self, *, kind: str | None = None, query: str | None = None) -> list[Asset]: ...
    def add_watchlist(self, item: WatchlistItem) -> bool: ...
    def remove_watchlist(self, asset_id: str) -> bool: ...
    def list_watchlist(self) -> list[WatchlistItem]: ...

    # -- articles (durable, append-only) ----------------------------------- #
    def add_articles(self, articles: list[Article]) -> int: ...
    def get_article(self, article_id: str) -> Article | None: ...
    def list_articles(
        self,
        *,
        since: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[Article]: ...

    # -- derived rows ------------------------------------------------------ #
    def replace_derived(
        self,
        clusters: list[Cluster],
        events: list[Event],
        links: list[EventLink],
    ) -> None: ...
    def list_clusters(self) -> list[Cluster]: ...
    def get_event(self, event_id: str) -> Event | None: ...
    def list_events(
        self,
        *,
        event_type: str | None = None,
        asset_id: str | None = None,
        stage: str | None = None,
        since: str | None = None,
    ) -> list[Event]: ...
    def list_links(self, event_id: str | None = None) -> list[EventLink]: ...

    # -- signals (durable, append-only, versioned) ------------------------- #
    def add_signals(self, signals: list[Signal]) -> int: ...
    def get_signal(self, signal_id: str) -> Signal | None: ...
    def list_signals(
        self,
        *,
        asset_id: str | None = None,
        direction: str | None = None,
        min_confidence: float | None = None,
        since: str | None = None,
    ) -> list[Signal]: ...
    def signals_for_key(self, signal_key: str) -> list[Signal]: ...
    def add_aliases(self, aliases: list[SignalKeyAlias]) -> int: ...
    def list_aliases(self) -> list[SignalKeyAlias]: ...

    # -- briefs ------------------------------------------------------------ #
    def add_briefs(self, briefs: list[Brief]) -> int: ...
    def get_brief(self, signal_id: str) -> Brief | None: ...

    # -- prices ------------------------------------------------------------ #
    def add_price_bars(self, bars: list[PriceBar]) -> int: ...
    def list_price_bars(
        self,
        *,
        asset_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[PriceBar]: ...

    # -- backtests --------------------------------------------------------- #
    def add_backtest(self, run: BacktestRun, results: list[BacktestResult]) -> None: ...
    def get_backtest(self, run_id: str) -> BacktestRun | None: ...
    def list_backtests(self, limit: int | None = None) -> list[BacktestRun]: ...
    def list_backtest_results(self, run_id: str) -> list[BacktestResult]: ...


__all__ = ["Repository", "RepositoryError"]
