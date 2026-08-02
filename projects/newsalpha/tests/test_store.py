"""Store: the repository interface, its SQLite backend and its in-memory twin.

Every behavioural test runs against *both* backends, so the in-memory repository
used by tests and evals cannot drift from the SQLite one used in production.
"""

from __future__ import annotations

import sqlite3

import pytest
from newsalpha.engine import pipeline
from newsalpha.models import BacktestParams, BarSource, PriceBar
from newsalpha.service import NewsAlphaService
from newsalpha.store import InMemoryRepository, Repository, RepositoryError, SQLiteRepository
from newsalpha_testkit import bars, raw

AS_OF = "2026-03-20T07:00:00Z"


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    repo = (
        InMemoryRepository()
        if request.param == "memory"
        else SQLiteRepository(tmp_path / "newsalpha.db")
    )
    repo.initialize()
    yield repo
    repo.close()


@pytest.fixture
def service(repository, datasets):
    service = NewsAlphaService(repository, datasets)
    service.initialize()
    return service


def corpus():
    return [
        raw(
            "hack",
            "Aave bridge hack drains $47 million",
            "The Aave protocol was exploited in a bridge hack that drained $47 million.",
            domain="coindesk.example",
            published_at="2026-03-10T08:00:00Z",
        ),
        raw(
            "earn",
            "Nvidia beat estimates",
            "Nvidia beat estimates for the quarter as revenue climbed.",
            domain="cnbc.example",
            published_at="2026-03-11T09:00:00Z",
        ),
    ]


def test_store_both_backends_satisfy_the_protocol(repository):
    assert isinstance(repository, Repository)


def test_store_assets_load_at_init(service, datasets):
    assert len(service.repository.list_assets()) == len(datasets.assets)
    assert service.repository.get_asset("eq:AAPL").name == "Apple Inc."
    assert {a.id for a in service.repository.list_assets(kind="index")} == {"idx:US", "idx:CX"}
    assert "cx:BTC" in {a.id for a in service.repository.list_assets(query="bitcoin")}
    assert [a.id for a in service.repository.list_assets(query="nvidia")] == ["eq:NVDA"]


def test_store_articles_are_append_only_and_idempotent(service, datasets):
    articles = pipeline.prepare_articles(corpus(), [], datasets, AS_OF)
    assert service.repository.add_articles(articles) == 2
    assert service.repository.add_articles(articles) == 0
    assert len(service.repository.list_articles()) == 2
    assert service.repository.list_articles(domain="cnbc.example")[0].source_domain == (
        "cnbc.example"
    )
    assert len(service.repository.list_articles(limit=1)) == 1


def test_store_derived_rows_are_replaced_wholesale(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    service.ingest(StaticNewsFeed(corpus()), as_of=AS_OF)
    before = {c.id for c in service.repository.list_clusters()}
    assert before
    service.repository.replace_derived([], [], [])
    assert service.repository.list_clusters() == []
    assert service.repository.list_events() == []
    assert service.repository.list_links() == []


def test_store_signals_and_briefs_round_trip(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    result = service.ingest(StaticNewsFeed(corpus()), as_of=AS_OF)
    assert result.signals_new >= 2
    signals = service.repository.list_signals()
    assert signals
    for signal in signals:
        stored = service.repository.get_signal(signal.id)
        assert stored == signal
        brief = service.repository.get_brief(signal.id)
        assert brief is not None and brief.frame_checked is True
        assert service.repository.signals_for_key(signal.signal_key)[-1].revision >= 1


def test_store_signal_revision_uniqueness_is_enforced(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    service.ingest(StaticNewsFeed(corpus()), as_of=AS_OF)
    signal = service.repository.list_signals()[0]
    clash = signal.model_copy(update={"id": "f" * 16})
    with pytest.raises(RepositoryError):
        service.repository.add_signals([clash])


def test_store_brief_requires_its_signal(repository, datasets):
    from newsalpha.models import Brief

    brief = Brief(
        signal_id="a" * 16,
        template_id="t",
        what_happened="x",
        why_it_matters="y",
        what_to_watch=("z",),
        uncertainty_note="w",
        rendered_text="x y z w",
    )
    with pytest.raises((RepositoryError, sqlite3.IntegrityError)):
        repository.add_briefs([brief])


def test_store_price_bars_are_unique_per_asset_and_date(repository):
    series = bars("cx:BTC", "2026-03-01", 5)
    assert repository.add_price_bars(series) == 5
    assert repository.add_price_bars(series) == 0
    assert len(repository.list_price_bars(asset_id="cx:BTC")) == 5
    assert len(repository.list_price_bars(start="2026-03-03")) == 3


def test_store_fixture_bars_are_never_overwritten_by_live_loads(repository):
    fixture_bar = PriceBar(
        asset_id="cx:BTC",
        date="2026-03-01",
        open=1,
        high=2,
        low=1,
        close=2,
        volume=0,
        source=BarSource.fixture,
    )
    live_bar = fixture_bar.model_copy(update={"close": 1.5, "source": BarSource.live})
    repository.add_price_bars([fixture_bar])
    repository.add_price_bars([live_bar])
    stored = repository.list_price_bars(asset_id="cx:BTC")[0]
    assert stored.source is BarSource.fixture
    assert stored.close == 2


def test_store_watchlist_add_remove_list(service):
    assert service.add_watch("eq:AAPL", AS_OF) is True
    assert service.add_watch("eq:AAPL", AS_OF) is False
    assert [item.asset_id for item in service.repository.list_watchlist()] == ["eq:AAPL"]
    assert service.remove_watch("eq:AAPL") is True
    assert service.remove_watch("eq:AAPL") is False


def test_fr11_unknown_watchlist_id_is_rejected_with_a_suggestion(service):
    with pytest.raises(RepositoryError, match="eq:AAPL"):
        service.add_watch("eq:AAPLE", AS_OF)
    assert service.suggest_asset("bitcoin") == "cx:BTC"


def test_store_backtest_runs_and_results_round_trip(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    service.ingest(StaticNewsFeed(corpus()), as_of=AS_OF)
    service.repository.add_price_bars(
        [
            *bars("cx:AAVE", "2026-03-01", 40, drift=-0.01),
            *bars("idx:CX", "2026-03-01", 40),
            *bars("eq:NVDA", "2026-03-01", 40, drift=0.01),
            *bars("idx:US", "2026-03-01", 40),
        ]
    )
    run, results = service.run_backtest(BacktestParams(start="2026-03-01", end="2026-03-31"), AS_OF)
    assert service.repository.get_backtest(run.id).id == run.id
    assert len(service.repository.list_backtest_results(run.id)) == len(results)
    assert service.repository.list_backtests(limit=1)[0].id == run.id
    assert run.aggregates.n >= 1


def test_store_reset_recreates_the_database(service):
    service.repository.add_price_bars(bars("cx:BTC", "2026-03-01", 3))
    service.initialize(reset=True)
    assert service.repository.list_price_bars() == []
    assert service.repository.list_assets() != []


def test_store_service_ingest_is_idempotent_end_to_end(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    feed = StaticNewsFeed(corpus())
    first = service.ingest(feed, as_of=AS_OF)
    second = service.ingest(feed, as_of=AS_OF)
    assert second.articles_new == 0
    assert second.signals_new == 0
    assert second.revisions_new == 0
    assert first.clusters == second.clusters


def test_store_digest_reads_through_the_service(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    service.ingest(StaticNewsFeed(corpus()), as_of=AS_OF)
    service.add_watch("eq:NVDA", AS_OF)
    digest = service.digest(as_of_date="2026-03-11")
    assert {entry.asset_id for entry in digest.entries} == {"eq:NVDA"}
    everything = service.digest(as_of_date="2026-03-11", watchlist_only=False)
    assert len(everything.entries) >= 2


def test_store_signal_revisions_returns_the_whole_chain(service, datasets):
    from newsalpha.adapters import StaticNewsFeed

    service.ingest(StaticNewsFeed(corpus()), as_of=AS_OF)
    signal = service.repository.list_signals()[0]
    chain = service.signal_revisions(signal.id)
    assert [s.revision for s in chain] == sorted(s.revision for s in chain)
    assert service.signal_revisions("0" * 16) == []


def test_fr15_active_window_days_comes_from_the_environment(repository, datasets):
    from newsalpha.service import active_window_days_from_env

    assert active_window_days_from_env({}) == 30
    assert active_window_days_from_env({"NEWSALPHA_ACTIVE_WINDOW_DAYS": "7"}) == 7
    with pytest.raises(ValueError, match="must be an integer"):
        active_window_days_from_env({"NEWSALPHA_ACTIVE_WINDOW_DAYS": "soon"})
    with pytest.raises(ValueError, match="must be >= 1"):
        active_window_days_from_env({"NEWSALPHA_ACTIVE_WINDOW_DAYS": "0"})

    service = NewsAlphaService(repository, datasets, environ={"NEWSALPHA_ACTIVE_WINDOW_DAYS": "3"})
    assert service.active_window_days == 3


def test_store_default_db_path_honours_the_env_var():
    from newsalpha.store import DEFAULT_DB_PATH, default_db_path

    assert default_db_path({}) == DEFAULT_DB_PATH
    assert str(default_db_path({"NEWSALPHA_DB": "/tmp/x.db"})) == "/tmp/x.db"
