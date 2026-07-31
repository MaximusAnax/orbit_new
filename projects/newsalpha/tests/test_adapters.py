"""Adapters: offline implementations, env gating of the live ones, hermeticity."""

from __future__ import annotations

import sys
from datetime import date, datetime

import pytest
from newsalpha.adapters import (
    FixtureMarketData,
    FixtureNewsFeed,
    MarketData,
    MarketDataError,
    NewsFeed,
    NewsFeedError,
    StaticMarketData,
    StaticNewsFeed,
    resolve_marketdata,
    resolve_newsfeed,
)
from newsalpha.adapters.marketdata_fixture import filename_for
from newsalpha.models import BarSource, RawArticle
from newsalpha_testkit import bars, utc

CORPUS = [
    {
        "external_id": "a",
        "url": "https://reuters.example/a",
        "source_domain": "reuters.example",
        "published_at": "2026-03-10T08:00:00Z",
        "fetched_at": "2026-03-10T09:00:00Z",
        "title": "Nvidia beat estimates",
        "body": "Nvidia beat estimates for the quarter.",
        "family": "dev",
    },
    {
        "external_id": "b",
        "source_domain": "cnbc.example",
        "published_at": "2026-03-12T08:00:00Z",
        "title": "Solana lists",
        "body": "Solana is now available on Coinbase.",
    },
]


@pytest.fixture
def corpus_path(tmp_path):
    import json

    path = tmp_path / "articles.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in CORPUS) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def market_dir(tmp_path):
    directory = tmp_path / "market"
    directory.mkdir()
    for asset_id in ("cx:BTC", "idx:CX"):
        rows = ["date,open,high,low,close,volume"]
        rows += [
            f"{bar.date},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}"
            for bar in bars(asset_id, "2026-03-01", 5, drift=0.01)
        ]
        (directory / filename_for(asset_id)).write_text("\n".join(rows) + "\n")
    return directory


# --------------------------------------------------------------------------- #
# Offline news feed
# --------------------------------------------------------------------------- #


def test_fixture_feed_satisfies_the_protocol(corpus_path):
    assert isinstance(FixtureNewsFeed(corpus_path), NewsFeed)


def test_fixture_feed_reads_the_corpus_in_deterministic_order(corpus_path):
    feed = FixtureNewsFeed(corpus_path)
    articles = feed.fetch(utc("2026-03-01T00:00:00Z"), utc("2026-03-31T00:00:00Z"))
    assert [a.external_id for a in articles] == ["a", "b"]
    assert articles[0].source_domain == "reuters.example"
    assert articles[1].fetched_at == articles[1].published_at  # defaulted, documented


def test_fixture_feed_filters_by_window(corpus_path):
    feed = FixtureNewsFeed(corpus_path)
    articles = feed.fetch(utc("2026-03-11T00:00:00Z"), utc("2026-03-31T00:00:00Z"))
    assert [a.external_id for a in articles] == ["b"]


def test_fixture_feed_reports_a_missing_or_malformed_corpus(tmp_path):
    with pytest.raises(NewsFeedError, match="not found"):
        FixtureNewsFeed(tmp_path / "missing.jsonl").load()

    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"external_id": "a"}\n', encoding="utf-8")
    with pytest.raises(NewsFeedError, match="missing required field"):
        FixtureNewsFeed(broken).load()


def test_static_feed_is_deterministic():
    feed = StaticNewsFeed(
        [
            RawArticle(
                external_id="z",
                source_domain="a.example",
                published_at="2026-03-12T00:00:00Z",
                fetched_at="2026-03-12T00:00:00Z",
                title="t",
                body="b",
            ),
            RawArticle(
                external_id="a",
                source_domain="a.example",
                published_at="2026-03-10T00:00:00Z",
                fetched_at="2026-03-10T00:00:00Z",
                title="t",
                body="b",
            ),
        ]
    )
    got = feed.fetch(utc("2026-03-01T00:00:00Z"), utc("2026-03-31T00:00:00Z"))
    assert [a.external_id for a in got] == ["a", "z"]


# --------------------------------------------------------------------------- #
# Offline market data
# --------------------------------------------------------------------------- #


def test_fixture_market_data_satisfies_the_protocol(market_dir):
    assert isinstance(FixtureMarketData(market_dir), MarketData)


def test_fixture_market_data_reads_committed_csvs(market_dir):
    market = FixtureMarketData(market_dir)
    series = market.daily_bars("cx:BTC", date(2026, 3, 1), date(2026, 3, 31))
    assert len(series) == 5
    assert series[0].source is BarSource.fixture
    assert [bar.date for bar in series] == sorted(bar.date for bar in series)
    assert market.available_assets() == ["cx:BTC", "idx:CX"]


def test_fixture_market_data_filters_by_date(market_dir):
    market = FixtureMarketData(market_dir)
    assert len(market.daily_bars("cx:BTC", date(2026, 3, 3), date(2026, 3, 4))) == 2


def test_fixture_market_data_reports_a_missing_series(market_dir):
    with pytest.raises(MarketDataError, match="no committed price series"):
        FixtureMarketData(market_dir).daily_bars("eq:AAPL", date(2026, 3, 1), date(2026, 3, 2))


def test_fixture_market_data_rejects_a_bad_header(tmp_path):
    directory = tmp_path / "bad"
    directory.mkdir()
    (directory / filename_for("cx:BTC")).write_text("d,o,h,l,c,v\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="expected header"):
        FixtureMarketData(directory).daily_bars("cx:BTC", date(2026, 3, 1), date(2026, 3, 2))


def test_static_market_data_round_trips():
    market = StaticMarketData(bars("cx:BTC", "2026-03-01", 3))
    assert len(market.daily_bars("cx:BTC", date(2026, 3, 1), date(2026, 3, 3))) == 3
    assert market.daily_bars("cx:ETH", date(2026, 3, 1), date(2026, 3, 3)) == []


# --------------------------------------------------------------------------- #
# Live adapters: env-gated, lazily imported
# --------------------------------------------------------------------------- #


def test_live_rss_feed_stays_inactive_without_the_env_var():
    with pytest.raises(NewsFeedError, match="NEWSALPHA_FEEDS"):
        resolve_newsfeed("rss", environ={})


def test_live_market_data_stays_inactive_without_the_env_var():
    with pytest.raises(MarketDataError, match="NEWSALPHA_LIVE"):
        resolve_marketdata("live", environ={}, benchmarks={})


def test_resolve_newsfeed_returns_the_fixture_adapter(corpus_path):
    assert isinstance(resolve_newsfeed("fixture", path=corpus_path), FixtureNewsFeed)
    with pytest.raises(NewsFeedError, match="unknown feed kind"):
        resolve_newsfeed("carrier-pigeon")


def test_resolve_marketdata_returns_the_fixture_adapter(market_dir):
    assert isinstance(resolve_marketdata("fixture", directory=market_dir), FixtureMarketData)
    with pytest.raises(MarketDataError, match="unknown market data source"):
        resolve_marketdata("smoke-signals")


def test_hermetic_no_live_adapters_on_the_offline_path(corpus_path, market_dir, datasets):
    """FR-14: the offline path never imports a live adapter or `feedparser`."""
    for module in (
        "feedparser",
        "newsalpha.adapters.newsfeed_rss",
        "newsalpha.adapters.marketdata_live",
    ):
        sys.modules.pop(module, None)

    from newsalpha.engine import pipeline
    from newsalpha.store import InMemoryRepository

    feed = resolve_newsfeed("fixture", path=corpus_path)
    market = resolve_marketdata("fixture", directory=market_dir)
    articles = feed.fetch(utc("2026-03-01T00:00:00Z"), utc("2026-03-31T00:00:00Z"))
    fresh, result = pipeline.ingest(list(articles), datasets, as_of="2026-03-20T07:00:00Z")
    repository = InMemoryRepository()
    repository.initialize()
    repository.add_articles(fresh)
    repository.add_price_bars(market.daily_bars("cx:BTC", date(2026, 3, 1), date(2026, 3, 5)))
    assert result.clusters

    for module in (
        "feedparser",
        "newsalpha.adapters.newsfeed_rss",
        "newsalpha.adapters.marketdata_live",
    ):
        assert module not in sys.modules, module


def test_live_rss_entry_mapping_without_network():
    """The live adapter is a real code path: entry -> RawArticle, no network involved."""
    from newsalpha.adapters.newsfeed_rss import RSSNewsFeed

    feed = RSSNewsFeed(
        ["https://reuters.example/rss"],
        environ={"NEWSALPHA_FEEDS": "https://reuters.example/rss"},
        fetched_at=datetime.fromisoformat("2026-03-10T09:00:00+00:00"),
    )

    class Entry:
        id = "https://reuters.example/a"
        link = "https://reuters.example/a"
        title = "Nvidia beat estimates"
        summary = "Nvidia beat estimates for the quarter."
        published_parsed = (2026, 3, 10, 8, 0, 0, 0, 0, 0)

    article = feed._to_article(Entry(), "https://reuters.example/rss", feed._fetched_at)
    assert article.external_id == "https://reuters.example/a"
    assert article.source_domain == "reuters.example"
    assert article.published_at == "2026-03-10T08:00:00Z"
    assert article.published_at_estimated is False


def test_live_rss_missing_timestamp_is_flagged_estimated():
    from newsalpha.adapters.newsfeed_rss import RSSNewsFeed

    feed = RSSNewsFeed(
        ["https://blog.example/rss"],
        environ={"NEWSALPHA_FEEDS": "https://blog.example/rss"},
        fetched_at=datetime.fromisoformat("2026-03-10T09:00:00+00:00"),
    )

    class Entry:
        id = "post-1"
        link = "https://blog.example/post-1"
        title = "A post"
        summary = "Body text."

    article = feed._to_article(Entry(), "https://blog.example/rss", feed._fetched_at)
    assert article.published_at_estimated is True
    assert article.published_at == "2026-03-10T09:00:00Z"


def test_live_market_data_routes_by_asset_kind_without_network():
    from newsalpha.adapters.marketdata_live import LiveMarketData

    captured: list[str] = []

    def opener(url: str, headers: dict[str, str]) -> str:
        captured.append(url)
        if "stooq" in url:
            return "Date,Open,High,Low,Close,Volume\n2026-03-02,10,11,9,10.5,1000\n"
        return (
            '{"prices": [[1772409600000, 100.0], [1772496000000, 101.0]], '
            '"total_volumes": [[1772409600000, 5.0], [1772496000000, 6.0]]}'
        )

    market = LiveMarketData(
        {"idx:US": ["live_proxy: SPY"], "idx:CX": ["cx:BTC"]},
        environ={"NEWSALPHA_LIVE": "1"},
        opener=opener,
    )
    equity = market.daily_bars("eq:AAPL", date(2026, 3, 1), date(2026, 3, 3))
    assert equity[0].asset_id == "eq:AAPL"
    assert equity[0].source is BarSource.live
    assert "aapl.us" in captured[0]

    crypto = market.daily_bars("cx:BTC", date(2026, 3, 1), date(2026, 3, 3))
    assert crypto and crypto[0].asset_id == "cx:BTC"
    assert "coins/bitcoin" in captured[1]

    assert market._us_proxy_symbol() == "spy.us"
    with pytest.raises(MarketDataError, match="no live route"):
        market.daily_bars("fx:EURUSD", date(2026, 3, 1), date(2026, 3, 3))


def test_live_crypto_benchmark_is_synthesized_from_the_basket():
    from newsalpha.adapters.marketdata_live import LiveMarketData

    def opener(url: str, headers: dict[str, str]) -> str:
        base = 100.0 if "bitcoin" in url else 50.0
        return (
            f'{{"prices": [[1772409600000, {base}], [1772496000000, {base * 1.1}], '
            f'[1772582400000, {base * 1.21}]], "total_volumes": []}}'
        )

    market = LiveMarketData(
        {"idx:CX": ["cx:BTC", "cx:ETH"]},
        environ={"NEWSALPHA_LIVE": "1"},
        opener=opener,
    )
    series = market.daily_bars("idx:CX", date(2026, 3, 1), date(2026, 3, 4))
    assert [bar.asset_id for bar in series] == ["idx:CX"] * 3
    assert series[0].close == 100.0
    assert series[1].close == pytest.approx(110.0, rel=1e-6)
    for bar in series:
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
