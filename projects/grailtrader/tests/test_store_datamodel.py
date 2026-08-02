"""Repository behaviour, exercised identically against both backends."""

from __future__ import annotations

import sqlite3

import pytest
from grailtrader.engine.events import merge_event, transition_status
from grailtrader.ids import advice_id, backtest_run_id
from grailtrader.models import (
    Advice,
    AdviceAction,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    EventSource,
    EventStatus,
    GarmentStatus,
    ListingStatus,
    ValuationMethod,
)
from grailtrader.store import InMemoryRepository, RepositoryError, SQLiteRepository
from grailtrader_testkit import (
    BRAND,
    LEAF,
    START,
    build,
    departure,
    flat_market,
    garment,
    sold_listing,
    week,
)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path):
    if request.param == "memory":
        store = InMemoryRepository()
    else:
        store = SQLiteRepository(tmp_path / "grailtrader.db")
    store.initialize(reset=True)
    yield store
    store.close()


def advice_row(garment_id: str, *, week_key: str, inputs_hash: str, created: str) -> Advice:
    return Advice(
        id=advice_id(garment_id, week_key, inputs_hash),
        garment_id=garment_id,
        as_of_week=week_key,
        inputs_hash=inputs_hash,
        config_version="1.0.0",
        stratum_id=LEAF,
        action=AdviceAction.BUY,
        is_candidate=True,
        horizon_weeks=4,
        expected_return=0.15,
        confidence=0.65,
        fair_value=1000.0,
        fair_value_method=ValuationMethod.REPEAT_SALES,
        rationale_codes=("driver:event:abc", "mod:q_index=1.0"),
        rendered_text="Action: buy ... footer",
        frame_checked=True,
        created_as_of=created,
    )


def test_store_gazetteer_round_trips(repo, ctx):
    repo.replace_gazetteer(ctx.gazetteer.brands)
    brands = repo.list_brands()
    assert len(brands) == len(ctx.gazetteer.brands)
    hl = repo.get_brand(BRAND)
    assert hl is not None
    assert [era.id for era in hl.eras] == [era.id for era in ctx.gazetteer.eras_for(BRAND)]


def test_store_listings_are_append_only_and_deduplicated(repo):
    listings = flat_market(weeks=2, per_week=3)
    assert repo.add_listings(listings) == len(listings)
    assert repo.add_listings(listings) == 0
    assert len(repo.list_listings()) == len(listings)
    assert repo.listing_ids() == {listing.id for listing in listings}
    assert repo.get_listing(listings[0].id) == listings[0]


def test_store_filters_listings_by_stratum_and_status(repo):
    sold = flat_market(weeks=1, per_week=3)
    repo.add_listings(sold)
    assert len(repo.list_listings(stratum=LEAF)) == 3
    assert repo.list_listings(stratum="celine") == []
    assert len(repo.list_listings(status=ListingStatus.SOLD)) == 3
    assert len(repo.list_listings(limit=2)) == 2


def test_store_index_points_are_replaced_wholesale(repo, ctx):
    index = build(flat_market(weeks=10, per_week=6), ctx, as_of_week=week(9))
    repo.replace_index_points(index.all_points())
    first = repo.list_index_points(stratum_id=LEAF)
    assert len(first) == len(index.points(LEAF))

    smaller = build(flat_market(weeks=6, per_week=6), ctx, as_of_week=week(5))
    repo.replace_index_points(smaller.all_points())
    assert len(repo.list_index_points(stratum_id=LEAF)) == len(smaller.points(LEAF))
    assert repo.list_index_points(from_week=week(2), to_week=week(3))


def test_store_events_upsert_status_and_corroboration(repo):
    event = departure(occurred_on=START, status=EventStatus.PENDING)
    repo.upsert_events([event])
    assert repo.get_event(event.id).status is EventStatus.PENDING

    confirmed = transition_status(event, EventStatus.CONFIRMED)
    repo.upsert_events([confirmed])
    stored = repo.get_event(event.id)
    assert stored.status is EventStatus.CONFIRMED
    assert stored.corroboration == 1

    second_feed = departure(
        occurred_on=START, source=EventSource.NEWS, refs=("https://www.wwd.com/a",)
    )
    repo.upsert_events([merge_event(stored, second_feed)])
    assert repo.get_event(event.id).corroboration == 2
    assert len(repo.list_events()) == 1


def test_store_lists_events_by_type_brand_and_status(repo):
    repo.upsert_events([departure(occurred_on=START)])
    assert len(repo.list_events(brand_id=BRAND)) == 1
    assert repo.list_events(brand_id="celine") == []
    assert len(repo.list_events(status=EventStatus.CONFIRMED)) == 1
    assert repo.list_events(status=EventStatus.PENDING) == []
    assert repo.list_events(since="2030-01-01") == []


def test_store_garment_soft_delete_keeps_the_row_readable(repo):
    piece = garment()
    repo.add_garment(piece)
    assert repo.list_garments() == [piece]
    with pytest.raises(RepositoryError):
        repo.add_garment(piece)

    removed = piece.model_copy(update={"deleted_at": "2026-02-01T00:00:00Z"})
    repo.update_garment(removed)
    assert repo.list_garments() == []
    assert repo.list_garments(include_deleted=True) == [removed]
    assert repo.get_garment(piece.id).deleted_at is not None


def test_store_garment_edits_persist(repo):
    piece = garment()
    repo.add_garment(piece)
    edited = piece.model_copy(update={"label": "renamed", "notes": "sleeve repair"})
    repo.update_garment(edited)
    assert repo.get_garment(piece.id).label == "renamed"
    with pytest.raises(RepositoryError):
        repo.update_garment(garment(anchor_date=week(9)))


def test_store_garment_status_filter(repo):
    owned = garment()
    watching = garment(status=GarmentStatus.WATCHING, anchor_date=week(1))
    repo.add_garment(owned)
    repo.add_garment(watching)
    assert repo.list_garments(status=GarmentStatus.WATCHING) == [watching]


def test_store_advice_is_append_only_and_supersedes_by_created_as_of(repo):
    piece = garment()
    repo.add_garment(piece)
    first = advice_row(
        piece.id, week_key=week(4), inputs_hash="aaa", created="2026-01-01T00:00:00Z"
    )
    assert repo.add_advice(first) is True
    assert repo.add_advice(first) is False  # byte-identical re-run is a no-op

    second = advice_row(
        piece.id, week_key=week(4), inputs_hash="bbb", created="2026-01-02T00:00:00Z"
    )
    repo.add_advice(second)
    current = repo.list_advice(garment_id=piece.id)
    assert [row.id for row in current] == [second.id]
    history = repo.list_advice(garment_id=piece.id, history=True)
    assert {row.id for row in history} == {first.id, second.id}
    assert repo.get_advice(first.id) == first


def test_store_advice_action_and_week_filters(repo):
    piece = garment()
    repo.add_garment(piece)
    row = advice_row(piece.id, week_key=week(4), inputs_hash="aaa", created="2026-01-01T00:00:00Z")
    repo.add_advice(row)
    assert repo.list_advice(action=AdviceAction.BUY) == [row]
    assert repo.list_advice(action=AdviceAction.SELL) == []
    assert repo.list_advice(as_of_week=week(4)) == [row]
    assert repo.list_advice(as_of_week=week(5)) == []


def test_store_refuses_a_non_frame_checked_advice(repo):
    piece = garment()
    repo.add_garment(piece)
    row = advice_row(piece.id, week_key=week(4), inputs_hash="aaa", created="2026-01-01T00:00:00Z")
    unchecked = row.model_copy(update={"frame_checked": False})
    with pytest.raises(RepositoryError, match="frame-checked"):
        repo.add_advice(unchecked)


def test_store_backtest_runs_and_results(repo):
    piece = garment()
    params = BacktestParams(
        start_week=week(1), end_week=week(9), reference="truth", scenario="unit"
    )
    run_id = backtest_run_id(params.model_dump(mode="json"), "2026-01-01T00:00:00Z")
    run = BacktestRun(
        id=run_id,
        params=params,
        as_of="2026-01-01T00:00:00Z",
        aggregates={"n_candidates": 1, "hit_rate": 1.0},
    )
    results = [
        BacktestResult(
            run_id=run_id,
            garment_id=piece.id,
            week=week(2),
            action=AdviceAction.BUY,
            is_candidate=True,
            horizon_weeks=4,
            confidence=0.7,
            expected_return=0.2,
            driver_event_ids=("abc",),
            entry_week=week(3),
            realized_return=0.18,
            hit=True,
        )
    ]
    repo.add_backtest(run, results)
    assert repo.get_backtest_run(run_id).aggregates["hit_rate"] == 1.0
    assert repo.list_backtest_results(run_id) == results
    assert [r.id for r in repo.list_backtest_runs(limit=1)] == [run_id]
    with pytest.raises(RepositoryError):
        repo.add_backtest(run, results)


def test_sqlite_schema_enforces_the_frame_check_constraint(tmp_path):
    store = SQLiteRepository(tmp_path / "db.sqlite")
    store.initialize(reset=True)
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO advice (id, garment_id, as_of_week, inputs_hash, config_version,"
            " stratum_id, action, is_candidate, horizon_weeks, expected_return, confidence,"
            " fair_value, fair_value_method, rationale_codes, rendered_text, frame_checked,"
            " created_as_of) VALUES ('x','g','2024-01-01','h','1.0.0','s','buy',1,4,0.2,0.7,"
            " 1.0,'repeat_sales','[]','text',0,'2026-01-01T00:00:00Z')"
        )
    store.close()


def test_sqlite_schema_enforces_usd_and_sold_ordering(tmp_path):
    store = SQLiteRepository(tmp_path / "db.sqlite")
    store.initialize(reset=True)
    listing = sold_listing(external_id="a", price=100.0, week_key=START)
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO listing (id, source, external_id, brand_id, era_id, category,"
            " condition, platform_label, size, title, status, listed_at, sold_at, ask_price,"
            " sold_price, currency) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                listing.id,
                "fixture",
                "a",
                BRAND,
                "helmut-lang:helmut",
                "outerwear",
                "excellent",
                "Excellent",
                None,
                None,
                "sold",
                "2024-06-03T00:00:00Z",
                "2024-05-01T00:00:00Z",
                None,
                100.0,
                "USD",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO listing (id, source, external_id, brand_id, era_id, category,"
            " condition, platform_label, size, title, status, listed_at, sold_at, ask_price,"
            " sold_price, currency) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "other",
                "fixture",
                "b",
                BRAND,
                "helmut-lang:helmut",
                "outerwear",
                "excellent",
                "Excellent",
                None,
                None,
                "sold",
                "2024-06-03T00:00:00Z",
                "2024-06-04T00:00:00Z",
                None,
                100.0,
                "EUR",
            ),
        )
    store.close()


def test_sqlite_reset_recreates_the_database(tmp_path, ctx):
    path = tmp_path / "db.sqlite"
    store = SQLiteRepository(path)
    store.initialize(reset=True)
    store.add_listings(flat_market(weeks=1, per_week=3))
    assert len(store.list_listings()) == 3
    store.initialize(reset=True)
    assert store.list_listings() == []
    store.close()
