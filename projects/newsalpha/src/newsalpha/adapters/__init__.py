"""Provider interfaces plus their offline implementations.

Only the Protocols and the offline implementations are exported here.  The live
adapters (`newsfeed_rss`, `marketdata_live`) are deliberately **not** imported:
importing this package must never pull them -- or their optional dependencies --
into `sys.modules` (FR-14 hermeticity).  Use
`newsalpha.adapters.resolve_newsfeed` / `resolve_marketdata`, which import the
live modules lazily and only when their env gate is set.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from .marketdata import MarketData, MarketDataError, is_weekday
from .marketdata_fixture import FixtureMarketData, StaticMarketData
from .newsfeed import NewsFeed, NewsFeedError
from .newsfeed_fixture import FixtureNewsFeed, StaticNewsFeed


def resolve_newsfeed(
    kind: str,
    *,
    path: Path | str | None = None,
    environ: dict[str, str] | None = None,
) -> NewsFeed:
    """`fixture` -> `FixtureNewsFeed`; `rss` -> the live adapter, imported lazily."""
    if kind == "fixture":
        if path is None:
            raise NewsFeedError("the fixture feed needs a corpus path")
        return FixtureNewsFeed(path)
    if kind == "rss":
        module = importlib.import_module("newsalpha.adapters.newsfeed_rss")
        env = environ if environ is not None else dict(os.environ)
        if not module.is_configured(env):
            raise NewsFeedError(f"{module.FEEDS_ENV} is not set: the live RSS feed stays inactive")
        return module.RSSNewsFeed(environ=env)
    raise NewsFeedError(f"unknown feed kind {kind!r}")


def resolve_marketdata(
    source: str,
    *,
    directory: Path | str | None = None,
    benchmarks: dict[str, list[str]] | None = None,
    environ: dict[str, str] | None = None,
) -> Any:
    """`fixture` -> `FixtureMarketData`; `live` -> the live adapter, imported lazily."""
    if source == "fixture":
        if directory is None:
            raise MarketDataError("the fixture market data needs a directory")
        return FixtureMarketData(directory)
    if source == "live":
        module = importlib.import_module("newsalpha.adapters.marketdata_live")
        env = environ if environ is not None else dict(os.environ)
        if not module.is_enabled(env):
            raise MarketDataError(
                f"{module.LIVE_ENV}=1 is required before live market data will run"
            )
        return module.LiveMarketData(benchmarks or {}, environ=env)
    raise MarketDataError(f"unknown market data source {source!r}")


__all__ = [
    "FixtureMarketData",
    "FixtureNewsFeed",
    "MarketData",
    "MarketDataError",
    "NewsFeed",
    "NewsFeedError",
    "StaticMarketData",
    "StaticNewsFeed",
    "is_weekday",
    "resolve_marketdata",
    "resolve_newsfeed",
]
